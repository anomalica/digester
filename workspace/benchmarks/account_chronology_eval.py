"""Offline evaluation for report-only narrative accounts and chronology."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from digester.account_score import _optimal_matches


SCHEMA = "anomalica/account-chronology-evaluation/1"
MANIFEST_SCHEMA = "anomalica/account-chronology-evaluation-manifest/1"
COORDINATE_SYSTEM = "media_time_ms"
CLAIM_DIGEST_ROOT = Path("../../../digests")
PRIVATE_BENCHMARK_ROOT = Path("private")
LEGACY_EVIDENCE_ROOT = Path("../../reports/accounts")


class EvaluationError(ValueError):
    """Account or chronology evidence is unsafe or incomplete."""


def _mapping(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise EvaluationError(f"cannot read {path}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"{path} is not a mapping")
    return value


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _confined_path(base: Path, raw: object, root: Path, field: str) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
        raise EvaluationError(f"{field} must be a relative path")
    allowed = (base / root).resolve()
    resolved = (base / raw).resolve()
    if not resolved.is_relative_to(allowed):
        raise EvaluationError(f"{field} escapes {root}")
    return resolved


def _milliseconds(location: str) -> int:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)-.+", location)
    if match is None:
        raise EvaluationError(f"invalid claim location {location!r}")
    try:
        milliseconds = (
            Decimal(match.group(1)) * 3600
            + Decimal(match.group(2)) * 60
            + Decimal(match.group(3))
        ) * 1000
    except InvalidOperation as exc:
        raise EvaluationError(f"invalid claim location {location!r}") from exc
    if milliseconds != milliseconds.to_integral_value():
        raise EvaluationError(
            f"claim location is not an exact millisecond: {location!r}"
        )
    return int(milliseconds)


def materialise_claims_from_digest(path: str | Path) -> list[dict]:
    """Enumerate canonical claims with exact media-start positions in milliseconds."""
    digest = _mapping(Path(path))
    record_hash = (digest.get("record") or {}).get("content_hash")
    if not _is_sha256(record_hash):
        raise EvaluationError("digest lacks a record content hash")
    materialised = []
    seen: set[str] = set()
    for section in ("domain_claims", "infrastructure_claims"):
        claims = digest.get(section) or []
        if not isinstance(claims, list):
            raise EvaluationError(f"digest {section} is not a list")
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("id"), str):
                raise EvaluationError(
                    f"digest {section} contains a claim without an id"
                )
            claim_id = claim["id"]
            if claim_id in seen:
                raise EvaluationError(f"digest has duplicate claim id {claim_id}")
            seen.add(claim_id)
            location = claim.get("location")
            position = (
                _milliseconds(location)
                if isinstance(location, str)
                and re.fullmatch(r"\d+:\d{2}:\d{2}(?:\.\d+)?-.+", location)
                else None
            )
            materialised.append(
                {
                    "id": claim_id,
                    "record_content_hash": record_hash,
                    "position": position,
                }
            )
    return materialised


def _normalise_spans(raw: object, account_id: str) -> list[tuple[int, int]]:
    if not isinstance(raw, list) or not raw:
        raise EvaluationError(f"account {account_id} needs exact spans")
    spans: list[tuple[int, int]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvaluationError(f"account {account_id} span {index} is not a mapping")
        start, end = item.get("start"), item.get("end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or start >= end
        ):
            raise EvaluationError(f"account {account_id} span {index} is invalid")
        spans.append((start, end))
    spans.sort()
    for previous, current in zip(spans, spans[1:]):
        if current[0] < previous[1]:
            raise EvaluationError(f"account {account_id} has overlapping spans")
    return spans


def _contains(spans: list[tuple[int, int]], position: int) -> bool:
    return any(start <= position < end for start, end in spans)


def _span_width(spans: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in spans)


def _validate_pairs(
    doc: dict,
    field: str,
    accounts: dict[str, list[tuple[int, int]]],
    assignments: dict[str, str | None],
) -> list[tuple[str, str, str]]:
    raw = doc.get(field, [])
    if not isinstance(raw, list):
        raise EvaluationError(f"{field} must be a list")
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvaluationError(f"{field}[{index}] is not a mapping")
        account = item.get("account_id")
        before = item.get("before_claim_id")
        after = item.get("after_claim_id")
        edge = (account, before, after)
        if account not in accounts or before == after or edge in seen:
            raise EvaluationError(f"{field}[{index}] has invalid endpoints")
        if assignments.get(before) != account or assignments.get(after) != account:
            raise EvaluationError(f"{field}[{index}] crosses an account boundary")
        seen.add(edge)
        out.append(edge)
    return out


def _validate_unordered_pairs(
    doc: dict,
    field: str,
    accounts: dict[str, list[tuple[int, int]]],
    assignments: dict[str, str | None],
) -> set[tuple[str, frozenset[str]]]:
    raw = doc.get(field, [])
    if not isinstance(raw, list):
        raise EvaluationError(f"{field} must be a list")
    out: set[tuple[str, frozenset[str]]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvaluationError(f"{field}[{index}] is not a mapping")
        account = item.get("account_id")
        claim_ids = item.get("claim_ids")
        if not isinstance(claim_ids, list) or len(claim_ids) != 2:
            raise EvaluationError(f"{field}[{index}] needs two claim_ids")
        pair = frozenset(claim_ids)
        if len(pair) != 2 or account not in accounts:
            raise EvaluationError(f"{field}[{index}] has invalid endpoints")
        if any(assignments.get(claim) != account for claim in pair):
            raise EvaluationError(f"{field}[{index}] crosses an account boundary")
        out.add((account, pair))
    return out


def validate_document(doc: dict, *, gold: bool) -> dict:
    """Validate exact record-scoped evidence and return normalised structures."""
    if doc.get("schema") != SCHEMA:
        raise EvaluationError("unsupported account-evaluation schema")
    record_hash = doc.get("record_content_hash")
    if (
        not isinstance(record_hash, str)
        or not record_hash.startswith("sha256:")
        or not _is_sha256(record_hash)
    ):
        raise EvaluationError("record_content_hash is required")
    for field in ("pre_digest_sha256", "digest_sha256"):
        if not _is_sha256(doc.get(field)):
            raise EvaluationError(f"{field} is required")
    if doc.get("coordinate_system") != COORDINATE_SYSTEM:
        raise EvaluationError(f"coordinate_system must be {COORDINATE_SYSTEM}")
    if gold and (not doc.get("reviewed_by") or not doc.get("reviewed_at")):
        raise EvaluationError("gold lacks human review provenance")

    raw_accounts = doc.get("accounts")
    if not isinstance(raw_accounts, list):
        raise EvaluationError("accounts must be a list")
    accounts: dict[str, list[tuple[int, int]]] = {}
    parents: dict[str, str] = {}
    for item in raw_accounts:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise EvaluationError("every account needs an id")
        account_id = item["id"]
        if account_id in accounts:
            raise EvaluationError(f"duplicate account id {account_id}")
        accounts[account_id] = _normalise_spans(item.get("spans"), account_id)
        if item.get("parent_account_id") is not None:
            parents[account_id] = item["parent_account_id"]
    for child, parent in parents.items():
        if parent not in accounts or parent == child:
            raise EvaluationError(f"account {child} has an invalid parent")
        if any(
            not any(ps <= cs and ce <= pe for ps, pe in accounts[parent])
            for cs, ce in accounts[child]
        ):
            raise EvaluationError(f"nested account {child} escapes parent {parent}")
    for account_id in parents:
        seen = {account_id}
        parent = parents.get(account_id)
        while parent is not None:
            if parent in seen:
                raise EvaluationError("account parent hierarchy contains a cycle")
            seen.add(parent)
            parent = parents.get(parent)

    raw_claims = doc.get("claims")
    if not isinstance(raw_claims, list):
        raise EvaluationError("claims must be a list")
    claims: dict[str, int | None] = {}
    claim_sequence: list[dict] = []
    for item in raw_claims:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise EvaluationError("every evaluated claim needs an id")
        claim_id = item["id"]
        if claim_id in claims:
            raise EvaluationError(f"duplicate claim id {claim_id}")
        if item.get("record_content_hash") != record_hash:
            raise EvaluationError(f"claim {claim_id} crosses a record boundary")
        position = item.get("position")
        if position is not None and (
            not isinstance(position, int) or isinstance(position, bool) or position < 0
        ):
            raise EvaluationError(f"claim {claim_id} has an invalid position")
        claims[claim_id] = position
        claim_sequence.append(
            {
                "id": claim_id,
                "record_content_hash": record_hash,
                "position": position,
            }
        )

    raw_assignments = doc.get("claim_assignments")
    if not isinstance(raw_assignments, list):
        raise EvaluationError("claim_assignments must be a list")
    assignments: dict[str, str | None] = {}
    for item in raw_assignments:
        if not isinstance(item, dict):
            raise EvaluationError("claim assignment is not a mapping")
        claim_id = item.get("claim_id")
        account_id = item.get("account_id")
        status = item.get("status")
        if claim_id not in claims or claim_id in assignments:
            raise EvaluationError("claim assignment has an invalid claim")
        position = claims[claim_id]
        if status == "bound":
            if account_id not in accounts or position is None:
                raise EvaluationError(f"claim {claim_id} has a forced binding")
            containing = [
                candidate
                for candidate, spans in accounts.items()
                if _contains(spans, position)
            ]
            if not containing or account_id not in containing:
                raise EvaluationError(f"claim {claim_id} is outside its account")
            narrowest = min(
                _span_width(accounts[candidate]) for candidate in containing
            )
            if (
                sum(
                    _span_width(accounts[candidate]) == narrowest
                    for candidate in containing
                )
                != 1
            ):
                raise EvaluationError(
                    f"claim {claim_id} has ambiguous account containment"
                )
            if _span_width(accounts[account_id]) != narrowest:
                raise EvaluationError(f"claim {claim_id} bypasses a nested account")
        elif status == "outside":
            if account_id is not None or position is None:
                raise EvaluationError(f"claim {claim_id} has invalid outside status")
            if any(_contains(spans, position) for spans in accounts.values()):
                raise EvaluationError(f"claim {claim_id} is inside an account")
        elif status == "unlocatable":
            if account_id is not None or position is not None:
                raise EvaluationError(
                    f"claim {claim_id} has invalid unlocatable status"
                )
        else:
            raise EvaluationError(f"claim {claim_id} has an invalid binding status")
        assignments[claim_id] = account_id
    if assignments.keys() != claims.keys():
        raise EvaluationError("every evaluated claim needs an explicit assignment")

    before = _validate_pairs(doc, "before_pairs", accounts, assignments)
    unknown = _validate_unordered_pairs(doc, "unknown_pairs", accounts, assignments)
    simultaneous = _validate_unordered_pairs(
        doc, "simultaneous_pairs", accounts, assignments
    )
    closure = _closure(before)
    if any(before == after for _, before, after in closure):
        raise EvaluationError("before_pairs contain a cycle")
    known_pairs = {
        (account, frozenset((before, after))) for account, before, after in closure
    }
    if unknown & simultaneous or known_pairs & (unknown | simultaneous):
        raise EvaluationError("chronology labels contradict each other")
    return {
        "record_content_hash": record_hash,
        "pre_digest_sha256": doc["pre_digest_sha256"].removeprefix("sha256:"),
        "digest_sha256": doc["digest_sha256"].removeprefix("sha256:"),
        "accounts": accounts,
        "claims": claims,
        "claim_sequence": claim_sequence,
        "assignments": assignments,
        "assignment_statuses": {
            item["claim_id"]: item["status"] for item in raw_assignments
        },
        "before": closure,
        "unknown": unknown,
        "simultaneous": simultaneous,
    }


def _closure(edges: list[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
    closure = set(edges)
    changed = True
    while changed:
        changed = False
        additions = {
            (account, before, after2)
            for account, before, after in closure
            for account2, before2, after2 in closure
            if account == account2 and after == before2
        }
        if not additions <= closure:
            closure.update(additions)
            changed = True
    return closure


def span_iou(left: list[tuple[int, int]], right: list[tuple[int, int]]) -> float:
    """Intersection-over-union across interrupted spans."""
    intersection = sum(
        max(0, min(le, re) - max(ls, rs)) for ls, le in left for rs, re in right
    )
    union = _span_width(left) + _span_width(right) - intersection
    return intersection / union if union else 0.0


def score(gold_doc: dict, predicted_doc: dict, *, iou_threshold: float = 0.5) -> dict:
    """Score account detection, exact binding and account-scoped chronology."""
    gold = validate_document(gold_doc, gold=True)
    predicted = validate_document(predicted_doc, gold=False)
    if gold["record_content_hash"] != predicted["record_content_hash"]:
        raise EvaluationError("gold and prediction target different records")
    if gold["pre_digest_sha256"] != predicted["pre_digest_sha256"]:
        raise EvaluationError("gold and prediction target different pre-digests")
    if gold["digest_sha256"] != predicted["digest_sha256"]:
        raise EvaluationError("gold and prediction target different digests")
    if gold["claim_sequence"] != predicted["claim_sequence"]:
        raise EvaluationError("gold and prediction evaluate different claims")

    gold_ids = list(gold["accounts"])
    predicted_ids = list(predicted["accounts"])
    candidates = []
    for gold_index, gold_id in enumerate(gold_ids):
        for predicted_index, predicted_id in enumerate(predicted_ids):
            iou = span_iou(
                gold["accounts"][gold_id], predicted["accounts"][predicted_id]
            )
            candidates.append(
                {
                    "gold_index": gold_index,
                    "predicted_index": predicted_index,
                    "eligible": iou >= iou_threshold,
                    "score": iou,
                }
            )
    matches = _optimal_matches(candidates, len(gold_ids), len(predicted_ids))
    predicted_to_gold = {
        predicted_ids[item["predicted_index"]]: gold_ids[item["gold_index"]]
        for item in matches
    }
    matched_ious = [item["score"] for item in matches]

    true_positive = len(matches)
    precision = (
        true_positive / len(predicted["accounts"]) if predicted["accounts"] else None
    )
    recall = true_positive / len(gold["accounts"]) if gold["accounts"] else None

    binding = {"correct": 0, "wrong_account": 0, "abstained": 0}
    for claim_id, expected in gold["assignments"].items():
        actual = predicted["assignments"][claim_id]
        mapped = predicted_to_gold.get(actual) if actual is not None else None
        expected_status = gold["assignment_statuses"][claim_id]
        actual_status = predicted["assignment_statuses"][claim_id]
        if expected is None and actual is None and expected_status == actual_status:
            binding["correct"] += 1
        elif expected is not None and mapped == expected:
            binding["correct"] += 1
        elif actual is None and expected is not None:
            binding["abstained"] += 1
        else:
            binding["wrong_account"] += 1

    predicted_before = {
        (predicted_to_gold.get(account), before, after)
        for account, before, after in predicted["before"]
        if account in predicted_to_gold
    }
    chronology = {
        "correct": 0,
        "wrong_direction": 0,
        "abstained": 0,
        "unknown_assertions": 0,
        "simultaneous_errors": 0,
        "unsupported_assertions": 0,
        "unmatched_account_assertions": 0,
    }
    for account, before, after in gold["before"]:
        if (account, before, after) in predicted_before:
            chronology["correct"] += 1
        elif (account, after, before) in predicted_before:
            chronology["wrong_direction"] += 1
        else:
            chronology["abstained"] += 1
    for account, before, after in predicted_before:
        pair = (account, frozenset((before, after)))
        if pair in gold["unknown"]:
            chronology["unknown_assertions"] += 1
        elif pair in gold["simultaneous"]:
            chronology["simultaneous_errors"] += 1
        elif (account, before, after) not in gold["before"] and (
            account,
            after,
            before,
        ) not in gold["before"]:
            chronology["unsupported_assertions"] += 1
    chronology["unmatched_account_assertions"] = sum(
        account not in predicted_to_gold for account, _, _ in predicted["before"]
    )

    return {
        "schema": SCHEMA,
        "record_content_hash": gold["record_content_hash"],
        "accounts": {
            "gold": len(gold["accounts"]),
            "predicted": len(predicted["accounts"]),
            "matched": true_positive,
            "precision": precision,
            "recall": recall,
            "mean_matched_span_iou": (
                sum(matched_ious) / len(matched_ious) if matched_ious else None
            ),
        },
        "claim_assignments": binding,
        "chronology": chronology,
    }


def audit_manifest(path: str | Path) -> dict:
    """Report what legacy evidence exists without pretending it is exact gold."""
    manifest = Path(path).resolve()
    doc = _mapping(manifest)
    if doc.get("schema") != MANIFEST_SCHEMA:
        raise EvaluationError("unsupported account-evaluation manifest")
    records = []
    for entry in doc.get("records") or []:
        digest_binding = entry.get("claim_digest")
        gold_binding = entry.get("authenticated_gold")
        prediction_binding = entry.get("scoreable_prediction")
        if not all(
            isinstance(value, dict)
            for value in (digest_binding, gold_binding, prediction_binding)
        ):
            raise EvaluationError("record lacks settled path bindings")
        if digest_binding.get("claim_materialiser") != (
            "account_chronology_eval.materialise_claims_from_digest"
        ):
            raise EvaluationError("claim_digest has an unsupported materialiser")
        if any(
            binding.get("coordinate_system") != COORDINATE_SYSTEM
            for binding in (digest_binding, gold_binding, prediction_binding)
        ):
            raise EvaluationError("path bindings use different coordinate systems")
        digest_path = _confined_path(
            manifest.parent,
            digest_binding.get("path"),
            CLAIM_DIGEST_ROOT,
            "claim_digest.path",
        )
        digest = _mapping(digest_path)
        actual_digest_sha256 = hashlib.sha256(digest_path.read_bytes()).hexdigest()
        expected = {
            "record_content_hash": entry.get("record_content_hash"),
            "pre_digest_sha256": (digest.get("pre_digest") or {}).get("sha256"),
            "digest_sha256": actual_digest_sha256,
        }
        if (digest.get("record") or {}).get("content_hash") != expected[
            "record_content_hash"
        ]:
            raise EvaluationError("claim digest targets a different record")
        for name, value in expected.items():
            if digest_binding.get(name) != value:
                raise EvaluationError(f"claim_digest.{name} does not match digest")
            if gold_binding.get(name) != value:
                raise EvaluationError(f"authenticated_gold.{name} is not digest-bound")
            if prediction_binding.get(name) != value:
                raise EvaluationError(
                    f"scoreable_prediction.{name} is not digest-bound"
                )
        _confined_path(
            manifest.parent,
            gold_binding.get("path"),
            PRIVATE_BENCHMARK_ROOT,
            "authenticated_gold.path",
        )
        if gold_binding.get("status") not in {"awaiting-human-review", "scoreable"}:
            raise EvaluationError("authenticated_gold has an invalid status")
        prediction_path = prediction_binding.get("path")
        if prediction_binding.get("status") == "blocked":
            if prediction_path is not None or not prediction_binding.get("reason"):
                raise EvaluationError("blocked prediction needs null path and reason")
        else:
            _confined_path(
                manifest.parent,
                prediction_path,
                PRIVATE_BENCHMARK_ROOT,
                "scoreable_prediction.path",
            )
        legacy_gold = _confined_path(
            manifest.parent,
            entry.get("legacy_gold_path"),
            LEGACY_EVIDENCE_ROOT,
            "legacy_gold_path",
        )
        legacy_prediction = _confined_path(
            manifest.parent,
            entry.get("legacy_prediction_path"),
            LEGACY_EVIDENCE_ROOT,
            "legacy_prediction_path",
        )
        gold = _mapping(legacy_gold)
        prediction = _mapping(legacy_prediction)
        materialised_claims = materialise_claims_from_digest(digest_path)
        records.append(
            {
                "record_content_hash": entry.get("record_content_hash"),
                "status": entry.get("status"),
                "claim_digest_path": str(digest_path),
                "authenticated_gold_path": str(
                    (manifest.parent / gold_binding["path"]).resolve()
                ),
                "scoreable_prediction_status": prediction_binding.get("status"),
                "materialised_claims": len(materialised_claims),
                "unlocatable_claims": sum(
                    claim["position"] is None for claim in materialised_claims
                ),
                "gold_accounts": len(gold.get("accounts") or []),
                "predicted_accounts": len(prediction.get("accounts") or []),
                "dropped_candidates": len(prediction.get("dropped") or []),
                "legacy_binding_counts": prediction.get("binding"),
                "legacy_reported_evidence": entry.get("evidence") or {},
                "maximum_account_recall_from_count": (
                    min(
                        len(gold.get("accounts") or []),
                        len(prediction.get("accounts") or []),
                    )
                    / len(gold.get("accounts") or [])
                    if gold.get("accounts")
                    else None
                ),
                "scoring_blockers": entry.get("scoring_blockers") or [],
            }
        )
    return {"schema": MANIFEST_SCHEMA, "records": records}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        default=Path(__file__).with_name("account-chronology-evaluation.yaml"),
    )
    args = parser.parse_args()
    print(json.dumps(audit_manifest(args.manifest), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
