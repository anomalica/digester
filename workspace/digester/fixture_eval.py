"""Behaviour-fixture evaluation for digestion experiments.

This complements span-based highlight evaluation with small, deliberately authored
narratives. Deterministic declared-term matching is the default; exact-input-bound
human or model adjudications can supply genuinely semantic matches.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from digester.eval import claims_of


SCHEMA = "anomalica/digestion-evaluation-fixtures/1"
PREDICTION_SET_SCHEMA = "anomalica/digestion-evaluation-predictions/1"
ADJUDICATION_SCHEMA = "anomalica/digestion-evaluation-adjudications/1"
DIMENSIONS = ("coverage", "context", "dates", "references", "accounts", "chronology")
RELATIONS = ("before", "simultaneous", "unknown")


class FixtureError(ValueError):
    """A fixture or prediction cannot be scored safely."""


@dataclass(frozen=True)
class ValidatedAdjudications:
    """Adjudications proven to target the exact fixture and prediction bytes."""

    judge_kind: str
    judge_identifier: str
    decisions: tuple[dict, ...]
    metadata: dict


def file_sha256(path: str | Path) -> str:
    """Return a prefixed SHA-256 over the file's exact bytes."""
    try:
        contents = Path(path).read_bytes()
    except OSError as exc:
        raise FixtureError(f"cannot read {path}") from exc
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def load_document(path: str | Path) -> dict:
    """Load a YAML or JSON mapping."""
    return load_hashed_document(path)[0]


def load_hashed_document(path: str | Path) -> tuple[dict, str]:
    """Load a mapping and hash the same bytes that were parsed."""
    path = Path(path)
    try:
        contents = path.read_bytes()
        value = yaml.safe_load(contents.decode())
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
        raise FixtureError(f"cannot read {path}") from exc
    if not isinstance(value, dict):
        raise FixtureError(f"{path} is not a mapping")
    return value, "sha256:" + hashlib.sha256(contents).hexdigest()


def load_predictions(
    path: str | Path, case_ids: list[str], *, case_id: str | None = None
) -> dict[str, dict]:
    """Load one case's digest or a manifest mapping cases to digest files."""
    return load_prediction_inputs(path, case_ids, case_id=case_id)[0]


def load_prediction_inputs(
    path: str | Path, case_ids: list[str], *, case_id: str | None = None
) -> tuple[dict[str, dict], dict[str, str]]:
    """Load case-bound digests and hashes over each digest's exact bytes."""
    path = Path(path)
    document, document_hash = load_hashed_document(path)
    selected = [item for item in case_ids if case_id is None or item == case_id]
    if not selected:
        raise FixtureError(f"unknown case {case_id}")
    if document.get("schema") != PREDICTION_SET_SCHEMA:
        if len(selected) != 1:
            raise FixtureError(
                "a direct digest needs --case when the fixture contains multiple cases"
            )
        return {selected[0]: document}, {selected[0]: document_hash}

    bindings = document.get("predictions")
    if not isinstance(bindings, dict):
        raise FixtureError("prediction manifest predictions must be a mapping")
    missing = set(selected) - set(bindings)
    if missing:
        raise FixtureError(f"prediction manifest lacks cases {sorted(missing)}")
    predictions = {}
    hashes = {}
    for selected_id in selected:
        raw = bindings[selected_id]
        if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
            raise FixtureError(
                f"prediction manifest case {selected_id} must be a relative path"
            )
        prediction_path = (path.parent / raw).resolve()
        if not prediction_path.is_relative_to(path.parent.resolve()):
            raise FixtureError(
                f"prediction manifest case {selected_id} escapes its directory"
            )
        predictions[selected_id], hashes[selected_id] = load_hashed_document(
            prediction_path
        )
    return predictions, hashes


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{field} must be a non-empty string")
    return value.strip()


def _items(value: object, field: str, *, minimum: int = 0) -> list:
    if not isinstance(value, list) or len(value) < minimum:
        raise FixtureError(f"{field} must be a list with at least {minimum} item(s)")
    return value


def _ids(items: list, field: str) -> set[str]:
    found: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise FixtureError(f"{field}[{index}] must be a mapping")
        item_id = _nonempty_string(item.get("id"), f"{field}[{index}].id")
        if item_id in found:
            raise FixtureError(f"duplicate {field} id {item_id}")
        found.add(item_id)
    return found


def validate_fixture(document: dict) -> dict:
    """Validate the additive fixture contract and return it unchanged."""
    if document.get("schema") != SCHEMA:
        raise FixtureError(f"schema must be {SCHEMA}")
    cases = _items(document.get("cases"), "cases", minimum=1)
    case_ids = _ids(cases, "cases")
    if len(case_ids) != len(cases):  # Defensive; _ids already reports the duplicate.
        raise FixtureError("case ids must be unique")

    for case_index, case in enumerate(cases):
        prefix = f"cases[{case_index}]"
        _nonempty_string(case.get("source"), f"{prefix}.source")
        expected = case.get("expectations")
        if not isinstance(expected, dict):
            raise FixtureError(f"{prefix}.expectations must be a mapping")

        concepts = _items(
            expected.get("concepts"), f"{prefix}.expectations.concepts", minimum=1
        )
        concept_ids = _ids(concepts, f"{prefix}.expectations.concepts")
        for index, concept in enumerate(concepts):
            terms = _items(
                concept.get("terms"), f"{prefix}.concepts[{index}].terms", minimum=1
            )
            for group_index, group in enumerate(terms):
                alternatives = group if isinstance(group, list) else [group]
                if not alternatives or not all(
                    isinstance(term, str) and term.strip() for term in alternatives
                ):
                    raise FixtureError(
                        f"{prefix}.concepts[{index}].terms[{group_index}] needs string alternatives"
                    )

        account_items = _items(
            expected.get("accounts", []), f"{prefix}.expectations.accounts"
        )
        account_ids = _ids(account_items, f"{prefix}.expectations.accounts")
        for dimension in ("contexts", "accounts"):
            entries = _items(
                expected.get(dimension, []), f"{prefix}.expectations.{dimension}"
            )
            if dimension == "contexts":
                _ids(entries, f"{prefix}.expectations.contexts")
            for index, entry in enumerate(entries):
                members = _items(
                    entry.get("concepts"),
                    f"{prefix}.{dimension}[{index}].concepts",
                    minimum=2,
                )
                unknown = set(members) - concept_ids
                if unknown:
                    raise FixtureError(
                        f"{prefix}.{dimension}[{index}] names unknown concepts {sorted(unknown)}"
                    )
                if dimension == "contexts":
                    referents = _items(
                        entry.get("referents"),
                        f"{prefix}.contexts[{index}].referents",
                        minimum=1,
                    )
                    if not all(
                        isinstance(item, str) and item.strip() for item in referents
                    ):
                        raise FixtureError(
                            f"{prefix}.contexts[{index}].referents must be strings"
                        )

        for dimension in ("dates", "references"):
            entries = _items(
                expected.get(dimension, []), f"{prefix}.expectations.{dimension}"
            )
            _ids(entries, f"{prefix}.expectations.{dimension}")
            for index, entry in enumerate(entries):
                if entry.get("concept") not in concept_ids:
                    raise FixtureError(
                        f"{prefix}.{dimension}[{index}] names an unknown concept"
                    )
                alternatives = _items(
                    entry.get("any_of"),
                    f"{prefix}.{dimension}[{index}].any_of",
                    minimum=1,
                )
                if dimension == "dates":
                    valid = all(
                        isinstance(item, str)
                        or (
                            isinstance(item, list)
                            and len(item) == 2
                            and all(isinstance(part, str) for part in item)
                        )
                        for item in alternatives
                    )
                else:
                    valid = all(
                        isinstance(item, str) and item.strip() for item in alternatives
                    )
                    if entry.get("kind") not in {"entity", "topic", "event"}:
                        raise FixtureError(
                            f"{prefix}.references[{index}].kind is invalid"
                        )
                if not valid:
                    raise FixtureError(
                        f"{prefix}.{dimension}[{index}].any_of is invalid"
                    )

        chronology = _items(
            expected.get("chronology", []), f"{prefix}.expectations.chronology"
        )
        _ids(chronology, f"{prefix}.expectations.chronology")
        for index, edge in enumerate(chronology):
            if edge.get("account") not in account_ids:
                raise FixtureError(
                    f"{prefix}.chronology[{index}] names an unknown account"
                )
            if edge.get("relation") not in RELATIONS:
                raise FixtureError(f"{prefix}.chronology[{index}].relation is invalid")
            pair = _items(
                edge.get("concepts"),
                f"{prefix}.chronology[{index}].concepts",
                minimum=2,
            )
            if len(pair) != 2 or pair[0] == pair[1] or set(pair) - concept_ids:
                raise FixtureError(
                    f"{prefix}.chronology[{index}].concepts must name two concepts"
                )
    return document


def validate_adjudications(
    document: dict,
    *,
    fixture_sha256: str,
    prediction_sha256: dict[str, str],
    fixture: dict,
    predictions: dict[str, dict],
) -> ValidatedAdjudications:
    """Validate semantic decisions against exact inputs and known identifiers."""
    validate_fixture(fixture)
    if set(prediction_sha256) != set(predictions):
        raise FixtureError("prediction hashes do not match loaded prediction cases")
    if document.get("schema") != ADJUDICATION_SCHEMA:
        raise FixtureError(f"adjudication schema must be {ADJUDICATION_SCHEMA}")
    if document.get("fixture_sha256") != fixture_sha256:
        raise FixtureError("adjudication fixture hash is stale")
    bound_predictions = document.get("prediction_sha256")
    if (
        not isinstance(bound_predictions, dict)
        or bound_predictions != prediction_sha256
    ):
        raise FixtureError("adjudication prediction hashes are stale")

    judge = document.get("judge")
    if not isinstance(judge, dict) or judge.get("kind") not in {"model", "human"}:
        raise FixtureError("adjudication judge.kind must be model or human")
    judge_identifier = _nonempty_string(
        judge.get("identifier"), "adjudication judge.identifier"
    )
    metadata = document.get("metadata", {})
    if not isinstance(metadata, dict):
        raise FixtureError("adjudication metadata must be a mapping")

    cases = {case["id"]: case for case in fixture["cases"]}
    decisions = _items(document.get("decisions"), "adjudication decisions")
    normalised = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for index, decision in enumerate(decisions):
        field = f"adjudication decisions[{index}]"
        if not isinstance(decision, dict):
            raise FixtureError(f"{field} must be a mapping")
        case_id = decision.get("case")
        if case_id not in prediction_sha256 or case_id not in cases:
            raise FixtureError(f"{field} names unknown case {case_id}")
        concepts = {
            concept["id"] for concept in cases[case_id]["expectations"]["concepts"]
        }
        concept_id = decision.get("concept")
        if concept_id not in concepts:
            raise FixtureError(f"{field} names unknown concept {concept_id}")
        claim_ids = _items(decision.get("claim_ids"), f"{field}.claim_ids", minimum=1)
        if not all(isinstance(claim_id, str) and claim_id for claim_id in claim_ids):
            raise FixtureError(f"{field}.claim_ids must be non-empty strings")
        if len(set(claim_ids)) != len(claim_ids):
            raise FixtureError(f"{field}.claim_ids contains duplicates")
        case_claims, _, _ = _prediction(predictions[case_id])
        known_claims = {claim["id"] for claim in case_claims}
        unknown_claims = set(claim_ids) - known_claims
        if unknown_claims:
            raise FixtureError(f"{field} names unknown claims {sorted(unknown_claims)}")
        verdict = decision.get("decision")
        if verdict not in {"equivalent", "not_equivalent", "abstain"}:
            raise FixtureError(f"{field}.decision is invalid")
        rationale = decision.get("rationale")
        evidence = decision.get("evidence")
        if not any(
            isinstance(value, str) and value.strip() for value in (rationale, evidence)
        ):
            raise FixtureError(f"{field} needs rationale or evidence")
        key = (case_id, concept_id, tuple(claim_ids))
        if key in seen:
            raise FixtureError(f"{field} duplicates an earlier decision")
        seen.add(key)
        normalised.append(
            {
                "case": case_id,
                "concept": concept_id,
                "claim_ids": list(claim_ids),
                "decision": verdict,
                **(
                    {"rationale": rationale.strip()}
                    if isinstance(rationale, str) and rationale.strip()
                    else {}
                ),
                **(
                    {"evidence": evidence.strip()}
                    if isinstance(evidence, str) and evidence.strip()
                    else {}
                ),
            }
        )
    return ValidatedAdjudications(
        judge_kind=judge["kind"],
        judge_identifier=judge_identifier,
        decisions=tuple(normalised),
        metadata=metadata,
    )


def _normalise(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def _contains(text: object, term: str) -> bool:
    return f" {_normalise(term)} " in f" {_normalise(text)} "


def _claim_matches(claim: dict, concept: dict) -> tuple[bool, list[str]]:
    text = claim.get("text") or claim.get("claim") or ""
    hits = []
    for raw_group in concept["terms"]:
        group = raw_group if isinstance(raw_group, list) else [raw_group]
        hit = next((term for term in group if _contains(text, term)), None)
        if hit is None:
            return False, hits
        hits.append(hit)
    return True, hits


def _prediction(
    document: dict,
) -> tuple[list[dict], dict[str, set[str]], dict[str, dict[str, set[tuple[str, str]]]]]:
    claims = claims_of(document)
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise FixtureError(f"prediction claim {index} is not a mapping")
        claim_id = _nonempty_string(claim.get("id"), f"prediction claim {index}.id")
        if claim_id in claim_ids:
            raise FixtureError(f"prediction has duplicate claim id {claim_id}")
        claim_ids.add(claim_id)

    annotations = document.get("evaluation") or {}
    if not isinstance(annotations, dict):
        raise FixtureError("prediction evaluation must be a mapping")
    accounts: dict[str, set[str]] = {}
    claim_accounts: dict[str, str] = {}
    for index, account in enumerate(
        _items(annotations.get("accounts", []), "prediction evaluation.accounts")
    ):
        if not isinstance(account, dict):
            raise FixtureError(f"prediction account {index} is not a mapping")
        account_id = _nonempty_string(
            account.get("id"), f"prediction account {index}.id"
        )
        members = set(
            _items(
                account.get("claim_ids"),
                f"prediction account {account_id}.claim_ids",
                minimum=1,
            )
        )
        if account_id in accounts or not all(isinstance(item, str) for item in members):
            raise FixtureError(f"prediction account {account_id} is invalid")
        if members - claim_ids:
            raise FixtureError(
                f"prediction account {account_id} names unknown claims {sorted(members - claim_ids)}"
            )
        repeated = members & claim_accounts.keys()
        if repeated:
            raise FixtureError(
                f"prediction claims belong to multiple accounts {sorted(repeated)}"
            )
        accounts[account_id] = members
        claim_accounts.update({claim_id: account_id for claim_id in members})

    edges: dict[str, dict[str, set[tuple[str, str]]]] = {}
    for index, item in enumerate(
        _items(annotations.get("chronology", []), "prediction evaluation.chronology")
    ):
        if not isinstance(item, dict) or item.get("account_id") not in accounts:
            raise FixtureError(
                f"prediction chronology {index} names an unknown account"
            )
        account_id = item["account_id"]
        if account_id in edges:
            raise FixtureError(
                f"prediction has duplicate chronology for account {account_id}"
            )
        labelled = {relation: set() for relation in RELATIONS}
        for relation in RELATIONS:
            for pair in _items(
                item.get(relation, []), f"prediction chronology {index}.{relation}"
            ):
                if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
                    raise FixtureError(
                        f"prediction chronology {index}.{relation} needs claim-id pairs"
                    )
                if set(pair) - accounts[account_id]:
                    raise FixtureError(
                        f"prediction chronology {index}.{relation} escapes its account"
                    )
                edge = tuple(pair)
                labelled[relation].add(
                    edge if relation == "before" else tuple(sorted(edge))
                )
        edges[account_id] = labelled
    return claims, accounts, edges


def _result(
    dimension: str, expectation: str, passed: bool, evidence: str, **details: Any
) -> dict:
    return {
        "dimension": dimension,
        "expectation": expectation,
        "passed": passed,
        "evidence": evidence,
        **details,
    }


def score_case(
    case: dict,
    prediction: dict,
    adjudications: ValidatedAdjudications | None = None,
) -> dict:
    """Score one prediction against one validated case."""
    claims, accounts, edges = _prediction(prediction)
    claims_by_id = {claim["id"]: claim for claim in claims}
    expected = case["expectations"]
    concepts = {item["id"]: item for item in expected["concepts"]}
    matches: dict[str, list[dict]] = {}
    hit_terms: dict[tuple[str, str], list[str]] = {}
    results: list[dict] = []
    semantic_decisions = (
        [item for item in adjudications.decisions if item["case"] == case["id"]]
        if adjudications
        else []
    )

    for concept_id, concept in concepts.items():
        deterministic = []
        for claim in claims:
            matched, terms = _claim_matches(claim, concept)
            if matched:
                deterministic.append(claim)
                hit_terms[(concept_id, claim["id"])] = terms
        concept_decisions = [
            item for item in semantic_decisions if item["concept"] == concept_id
        ]
        adjudicated_ids = {
            claim_id
            for item in concept_decisions
            if item["decision"] == "equivalent"
            for claim_id in item["claim_ids"]
        }
        found = list(deterministic)
        found.extend(
            claims_by_id[claim_id]
            for claim_id in sorted(adjudicated_ids)
            if claim_id not in {claim["id"] for claim in found}
        )
        matches[concept_id] = found
        evidence_parts = [
            f"deterministic {claim['id']} ({'; '.join(hit_terms[(concept_id, claim['id'])])})"
            for claim in deterministic
        ]
        decision_evidence = []
        for item in concept_decisions:
            reason = item.get("evidence") or item.get("rationale")
            detail = {
                **item,
                "judge_kind": adjudications.judge_kind,
                "judge_identifier": adjudications.judge_identifier,
            }
            decision_evidence.append(detail)
            evidence_parts.append(
                f"adjudicated {item['decision']} {','.join(item['claim_ids'])} "
                f"by {adjudications.judge_kind}:{adjudications.judge_identifier} ({reason})"
            )
        evidence = "; ".join(evidence_parts) or "no deterministic or adjudicated match"
        match_details = [
            {
                "claim_id": claim["id"],
                "source": (
                    "deterministic"
                    if claim in deterministic
                    else "semantic_adjudication"
                ),
            }
            for claim in found
        ]
        results.append(
            _result(
                "coverage",
                concept_id,
                bool(found),
                evidence,
                matches=match_details,
                adjudications=decision_evidence,
            )
        )

    def account_candidates(concept_ids: list[str]) -> list[str]:
        return [
            account_id
            for account_id, members in accounts.items()
            if all(
                any(claim["id"] in members for claim in matches[concept_id])
                for concept_id in concept_ids
            )
        ]

    for item in expected.get("contexts", []):
        candidates = account_candidates(item["concepts"])
        passing = []
        for account_id in candidates:
            members = accounts[account_id]
            context_text = " ".join(
                [
                    str(claim.get("text") or claim.get("claim") or "")
                    for claim in claims
                    if claim["id"] in members
                ]
                + [
                    str(ref.get("name") or "")
                    for claim in claims
                    if claim["id"] in members
                    for ref in claim.get("refs") or []
                    if isinstance(ref, dict)
                ]
            )
            if any(_contains(context_text, referent) for referent in item["referents"]):
                passing.append(account_id)
        evidence = (
            f"grouped in {passing}"
            if passing
            else f"candidate groups {candidates}; no required referent in grouped context"
        )
        results.append(_result("context", item["id"], bool(passing), evidence))

    for item in expected.get("dates", []):
        observed = []
        for claim in matches[item["concept"]]:
            value: Any = (
                claim.get("date_range")
                if claim.get("date_range") is not None
                else claim.get("date")
            )
            if value is not None:
                observed.append(value)
        passed = any(
            value == alternative for value in observed for alternative in item["any_of"]
        )
        results.append(
            _result(
                "dates",
                item["id"],
                passed,
                f"observed {observed or 'none'}; accepted {item['any_of']}",
            )
        )

    for item in expected.get("references", []):
        observed = sorted(
            {
                str(ref.get("name") or ref.get("id"))
                for claim in matches[item["concept"]]
                for ref in claim.get("refs") or []
                if isinstance(ref, dict) and (ref.get("name") or ref.get("id"))
            }
        )
        passed = any(
            any(_normalise(want) == _normalise(got) for got in observed)
            for want in item["any_of"]
        )
        results.append(
            _result(
                "references",
                item["id"],
                passed,
                f"{item['kind']} refs {observed or 'none'}; accepted {item['any_of']}",
            )
        )

    account_matches: dict[str, list[str]] = {}
    for item in expected.get("accounts", []):
        candidates = account_candidates(item["concepts"])
        account_matches[item["id"]] = candidates
        results.append(
            _result(
                "accounts",
                item["id"],
                bool(candidates),
                f"matching prediction accounts {candidates or 'none'}",
            )
        )

    for item in expected.get("chronology", []):
        left, right = item["concepts"]
        relation = item["relation"]
        found: list[str] = []
        for account_id in account_matches.get(item["account"], []):
            labelled = edges.get(account_id, {name: set() for name in RELATIONS})
            for left_claim in matches[left]:
                for right_claim in matches[right]:
                    pair = (left_claim["id"], right_claim["id"])
                    lookup = pair if relation == "before" else tuple(sorted(pair))
                    if lookup in labelled[relation]:
                        found.append(f"{account_id}:{pair[0]} {relation} {pair[1]}")
        results.append(
            _result(
                "chronology",
                item["id"],
                bool(found),
                "; ".join(found) or f"no {relation} edge for {left}, {right}",
            )
        )

    dimensions = {}
    for dimension in DIMENSIONS:
        rows = [item for item in results if item["dimension"] == dimension]
        passed = sum(item["passed"] for item in rows)
        dimensions[dimension] = {
            "passed": passed,
            "total": len(rows),
            "rate": passed / len(rows) if rows else None,
        }
    return {"case": case["id"], "dimensions": dimensions, "expectations": results}


def score(
    document: dict,
    predictions: dict[str, dict],
    *,
    case_id: str | None = None,
    adjudications: ValidatedAdjudications | None = None,
) -> dict:
    """Score case-bound digest predictions, optionally selecting one case."""
    validate_fixture(document)
    if adjudications is not None and not isinstance(
        adjudications, ValidatedAdjudications
    ):
        raise FixtureError("adjudications must be validated against exact inputs")
    cases = [
        case for case in document["cases"] if case_id is None or case["id"] == case_id
    ]
    if not cases:
        raise FixtureError(f"unknown case {case_id}")
    missing = {case["id"] for case in cases} - set(predictions)
    if missing:
        raise FixtureError(f"no prediction for cases {sorted(missing)}")
    case_results = [
        score_case(case, predictions[case["id"]], adjudications) for case in cases
    ]
    aggregate = {}
    for dimension in DIMENSIONS:
        passed = sum(
            result["dimensions"][dimension]["passed"] for result in case_results
        )
        total = sum(result["dimensions"][dimension]["total"] for result in case_results)
        aggregate[dimension] = {
            "passed": passed,
            "total": total,
            "rate": passed / total if total else None,
        }
    return {"schema": SCHEMA, "cases": case_results, "dimensions": aggregate}


def format_report(variants: list[dict]) -> str:
    """Render inspectable variant summaries and per-expectation evidence."""
    lines = []
    for variant in variants:
        lines.append(f"Variant: {variant['variant']}")
        for dimension in DIMENSIONS:
            summary = variant["dimensions"][dimension]
            rate = f"{summary['rate']:.0%}" if summary["rate"] is not None else "n/a"
            lines.append(
                f"  {dimension:10} {summary['passed']}/{summary['total']} ({rate})"
            )
        for case in variant["cases"]:
            lines.append(f"  Case: {case['case']}")
            for item in case["expectations"]:
                status = "PASS" if item["passed"] else "FAIL"
                lines.append(
                    f"    {status} {item['dimension']}/{item['expectation']}: {item['evidence']}"
                )
        lines.append("")
    lines.append(
        "Terms are matched deterministically first; semantic matches are explicitly labelled adjudicated."
    )
    return "\n".join(lines)


def json_report(variants: list[dict]) -> str:
    return json.dumps(
        {"schema": SCHEMA, "variants": variants}, indent=2, ensure_ascii=False
    )
