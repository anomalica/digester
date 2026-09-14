"""Validate the reviewed digest evaluation corpus without invoking a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import yaml

from digester.eval import grade_digest, parse_highlights
from digester.highlight_gold import (
    HighlightGoldError,
    validate as validate_highlight_gold,
)


SCHEMA = "anomalica/digest-evaluation-corpus/1"
HUMAN_GOLD = "human-reviewed"
MIGRATION_CANDIDATE = "migration-candidate"
HIGHLIGHTS_SCHEMA = "anomalica/highlights/1"
HIGHLIGHTS_PURPOSE = "digest-evaluation-claim-gold"
HIGHLIGHT_GOLD_SCHEMA = "anomalica/highlight-gold/1"
PERMISSION_SCHEMA = "anomalica/evaluation-rights-permission/1"
LOCAL_ANALYSIS = "local_information_analysis"


class CorpusValidationError(ValueError):
    """The corpus manifest or one of its evidence files is inconsistent."""


def _resolve(manifest: Path, raw: str) -> Path:
    return (manifest.parent / raw).resolve()


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(errors="replace")
    if not text.startswith("---\n"):
        raise CorpusValidationError(f"{path} has no YAML frontmatter")
    parts = text.split("---\n", 2)
    doc = yaml.safe_load(parts[1])
    if not isinstance(doc, dict):
        raise CorpusValidationError(f"{path} frontmatter is not a mapping")
    return doc, parts[2]


def _load_mapping(path: Path) -> dict:
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise CorpusValidationError(f"cannot read {path}") from exc
    if not isinstance(doc, dict):
        raise CorpusValidationError(f"{path} is not a mapping")
    return doc


def _validate_spans(raw: object, body: str, field: str, allow_overlap: bool) -> int:
    if not isinstance(raw, list):
        raise CorpusValidationError(f"external gold {field} must be a list")
    spans: list[tuple[int, int]] = []
    for index, span in enumerate(raw):
        if not isinstance(span, dict):
            raise CorpusValidationError(
                f"external gold {field}[{index}] is not a mapping"
            )
        start, end, text = span.get("start"), span.get("end"), span.get("text")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end > len(body)
            or start >= end
        ):
            raise CorpusValidationError(
                f"external gold {field}[{index}] has invalid offsets"
            )
        if not isinstance(text, str) or body[start:end] != text:
            raise CorpusValidationError(
                f"external gold {field}[{index}] text does not match the record body"
            )
        note = span.get("note")
        if note is not None and not isinstance(note, str):
            raise CorpusValidationError(
                f"external gold {field}[{index}] note is not text"
            )
        spans.append((start, end))
    if not allow_overlap:
        for previous, current in zip(sorted(spans), sorted(spans)[1:]):
            if current[0] < previous[1]:
                raise CorpusValidationError("external gold accepted spans overlap")
    return len(spans)


def _validate_ranges(raw: object, body: str) -> None:
    if raw is None:
        return
    if not isinstance(raw, list):
        raise CorpusValidationError("external gold complete_ranges must be a list")
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CorpusValidationError(
                f"external gold complete_ranges[{index}] is not a mapping"
            )
        start, end = item.get("start"), item.get("end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end > len(body)
            or start >= end
        ):
            raise CorpusValidationError(
                f"external gold complete_ranges[{index}] has invalid offsets"
            )
        note = item.get("note")
        if note is not None and not isinstance(note, str):
            raise CorpusValidationError(
                f"external gold complete_ranges[{index}] note is not text"
            )


def _validate_external_gold(doc: dict, entry: dict, body: str) -> int:
    slot = entry.get("slot")
    if doc.get("schema") != HIGHLIGHTS_SCHEMA:
        raise CorpusValidationError(f"{slot} external gold has unsupported schema")
    if doc.get("purpose") != HIGHLIGHTS_PURPOSE:
        raise CorpusValidationError(f"{slot} external gold has the wrong purpose")
    drafted_by = str(doc.get("drafted_by") or "").lower()
    if doc.get("provisional") is not False or "model" in drafted_by:
        raise CorpusValidationError(
            f"{slot} cannot admit provisional/model-drafted spans as gold"
        )

    expected_hash = str(entry.get("record_content_hash") or "")
    actual_hash = str(doc.get("record_hash") or "")
    if actual_hash.removeprefix("sha256:") != expected_hash.removeprefix("sha256:"):
        raise CorpusValidationError(f"{slot} external gold targets another record")
    expected_body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if doc.get("body_sha256") != expected_body_hash:
        raise CorpusValidationError(f"{slot} external gold body hash does not match")
    if not isinstance(doc.get("complete"), bool):
        raise CorpusValidationError(f"{slot} external gold lacks explicit completeness")

    reviewed_by = doc.get("reviewed_by")
    reviewed_at = doc.get("reviewed_at")
    if not isinstance(reviewed_by, str) or not reviewed_by.strip():
        raise CorpusValidationError(
            f"{slot} external gold lacks human review provenance"
        )
    if not isinstance(reviewed_at, str):
        raise CorpusValidationError(
            f"{slot} external gold lacks human review provenance"
        )
    try:
        timestamp = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CorpusValidationError(
            f"{slot} external gold has an invalid review timestamp"
        ) from exc
    if timestamp.tzinfo is None:
        raise CorpusValidationError(
            f"{slot} external gold has an invalid review timestamp"
        )

    accepted = _validate_spans(doc.get("spans"), body, "spans", False)
    _validate_spans(doc.get("rejected"), body, "rejected", True)
    _validate_ranges(doc.get("complete_ranges"), body)
    if not accepted:
        raise CorpusValidationError(f"{slot} external gold has no accepted spans")
    return accepted


def validate_claim_gold(entry: dict, record_body: str, manifest: Path) -> int:
    """Return accepted human gold units; provisional/model-drafted data returns 0."""
    gold = entry.get("claim_gold") or {}
    status = gold.get("status")
    mechanism = gold.get("mechanism")
    if status == "missing":
        if gold.get("path") is not None:
            raise CorpusValidationError(
                f"{entry.get('slot')} missing gold unexpectedly names a file"
            )
        return 0
    if status == "provisional-rejected":
        if mechanism != "external-spans" or not gold.get("path"):
            raise CorpusValidationError(
                f"{entry.get('slot')} provisional gold lacks external evidence"
            )
        provisional = _load_mapping(_resolve(manifest, gold["path"]))
        drafted_by = str(provisional.get("drafted_by") or "").lower()
        if provisional.get("provisional") is not True and "model" not in drafted_by:
            raise CorpusValidationError(
                f"{entry.get('slot')} rejected gold is not marked provisional/model-drafted"
            )
        spans = [
            span.get("text")
            for span in provisional.get("spans") or []
            if isinstance(span, dict) and span.get("text")
        ]
        grade = grade_digest(record_body, {"claims": []}, gold_texts=spans)
        if not spans or grade["unlocatable_gold"]:
            raise CorpusValidationError(
                f"{entry.get('slot')} provisional spans do not locate in the record"
            )
        return 0
    if status == MIGRATION_CANDIDATE:
        if mechanism != "in-body-highlights" or gold.get("path") is not None:
            raise CorpusValidationError(
                f"{entry.get('slot')} has invalid in-body migration evidence"
            )
        if not parse_highlights(record_body):
            raise CorpusValidationError(
                f"{entry.get('slot')} migration candidate has no highlights"
            )
        return 0
    if status != HUMAN_GOLD:
        raise CorpusValidationError(
            f"{entry.get('slot')} has unknown claim-gold status {status!r}"
        )
    if mechanism != "highlight-gold" or not gold.get("path"):
        raise CorpusValidationError(
            f"{entry.get('slot')} has unsupported human claim-gold evidence"
        )
    gold_path = _resolve(manifest, gold["path"])
    doc = _load_mapping(gold_path)
    try:
        state = validate_highlight_gold(
            str(entry.get("record_content_hash") or ""), record_body, doc
        )
    except HighlightGoldError as exc:
        raise CorpusValidationError(f"{entry.get('slot')} {exc}") from exc
    if not state["gold_facts"]:
        raise CorpusValidationError(
            f"{entry.get('slot')} highlight gold has no accepted facts"
        )
    return state["gold_units"]


def _permission_evidence(entry: dict, manifest: Path) -> dict | None:
    rights = entry.get("rights") or {}
    path = rights.get("permission_evidence_path")
    if not path:
        return None
    evidence = _load_mapping(_resolve(manifest, path))
    valid = (
        evidence.get("schema") == PERMISSION_SCHEMA
        and evidence.get("record_content_hash") == entry.get("record_content_hash")
        and isinstance(evidence.get("granted_by"), str)
        and bool(evidence["granted_by"].strip())
        and isinstance(evidence.get("granted_at"), str)
        and bool(evidence["granted_at"].strip())
    )
    return evidence if valid else None


def _permission_covers(
    entry: dict, manifest: Path, provider: str, route: str, use: str
) -> bool:
    evidence = _permission_evidence(entry, manifest)
    return bool(
        evidence
        and evidence.get("provider") == provider
        and evidence.get("route") == route
        and evidence.get("use") == use
    )


def authorise_dispatch(
    manifest_path: str | Path,
    record_path: str | Path,
    *,
    use: str,
    provider: str | None = None,
    route: str | None = None,
) -> dict:
    """Fail closed before evaluation input can reach a model transport."""
    manifest = Path(manifest_path).resolve()
    corpus = _load_mapping(manifest)
    if corpus.get("schema") != SCHEMA:
        raise CorpusValidationError("dispatch manifest has an unsupported schema")

    record, body = _frontmatter(Path(record_path).resolve())
    record_hash = record.get("content_hash")
    entry = next(
        (
            item
            for item in corpus.get("records") or []
            if item.get("record_content_hash") == record_hash
        ),
        None,
    )
    if entry is None:
        raise CorpusValidationError("record is not admitted by the evaluation manifest")

    rights = entry.get("rights") or {}
    if (record.get("copyright") or {}).get("status") != rights.get("status"):
        raise CorpusValidationError(
            "record rights do not match the evaluation manifest"
        )
    basis = rights.get("admission_basis")
    if not basis:
        raise CorpusValidationError("record has no evaluation rights basis")

    review = json.loads(_resolve(manifest, entry["review_path"]).read_text())
    coverage = review.get("observed_coverage")
    if (
        review.get("schema") != "anomalica/review-coverage/1"
        or not isinstance(coverage, (int, float))
        or isinstance(coverage, bool)
        or not 0 < coverage <= 1
    ):
        raise CorpusValidationError("record has no usable source review")
    gold_units = validate_claim_gold(entry, body, manifest)
    if not gold_units:
        raise CorpusValidationError("record has no authenticated claim gold")

    if use == "local-deterministic-grading":
        if basis == LOCAL_ANALYSIS:
            if rights.get("status") != "publicly_accessible" or not rights.get(
                "controlled_local_only"
            ):
                raise CorpusValidationError(
                    "local information analysis lacks its required controls"
                )
        elif basis in {"open_licence", "internal_evaluation_permission"}:
            if not _permission_covers(entry, manifest, "anomalica", "local", use):
                raise CorpusValidationError(
                    "local evaluation lacks record-specific permission evidence"
                )
        elif basis != "public_domain":
            raise CorpusValidationError("record is not admitted for local grading")
        complete = coverage == 1.0 and review.get("digestible") is True
        return {
            "record_content_hash": record_hash,
            "use": use,
            "scope": "whole-record" if complete else "partial-units-only",
            "gold_units": gold_units,
        }

    if use != "hosted-model-inference" or not provider or not route:
        raise CorpusValidationError("unknown or incomplete evaluation dispatch route")
    if coverage != 1.0 or review.get("digestible") is not True:
        raise CorpusValidationError("hosted evaluation requires complete source review")
    if basis == "public_domain":
        pass
    elif basis in {"open_licence", "internal_evaluation_permission"}:
        if not _permission_covers(entry, manifest, provider, route, use):
            raise CorpusValidationError(
                "hosted evaluation lacks record/provider/route permission evidence"
            )
    else:
        raise CorpusValidationError(
            f"{basis} does not permit hosted evaluation dispatch"
        )
    return {
        "record_content_hash": record_hash,
        "use": use,
        "provider": provider,
        "route": route,
        "scope": "whole-record",
        "gold_units": gold_units,
    }


def validate(manifest_path: str | Path) -> dict:
    """Validate corpus evidence and return its machine-readable readiness summary."""
    manifest = Path(manifest_path).resolve()
    doc = _load_mapping(manifest)
    if doc.get("schema") != SCHEMA:
        raise CorpusValidationError(f"unsupported corpus schema: {doc.get('schema')!r}")
    policy = doc.get("admission_policy") or {}
    rights_bases = set(policy.get("rights_bases") or [])
    if "internal_evaluation_permission" not in rights_bases:
        raise CorpusValidationError(
            "admission policy must support later internal-evaluation permission"
        )
    claim_policy = policy.get("claim_gold") or {}
    if claim_policy.get("accepted_mechanisms") != ["highlight-gold"]:
        raise CorpusValidationError(
            "only authenticated highlight-gold attestations may be admitted"
        )
    if (
        claim_policy.get("sidecar_schema") != HIGHLIGHT_GOLD_SCHEMA
        or claim_policy.get("in_body_highlights_are_gold") is not False
    ):
        raise CorpusValidationError("claim-gold admission policy is incomplete")

    summaries = []
    for entry in doc.get("records") or []:
        slot = entry.get("slot")
        expected_hash = entry.get("record_content_hash")
        rights = entry.get("rights") or {}
        if rights.get("admission_basis") not in rights_bases:
            raise CorpusValidationError(f"{slot} has no admitted rights basis")

        record_path = _resolve(manifest, entry["record_path"])
        review_path = _resolve(manifest, entry["review_path"])
        digest_path = _resolve(manifest, entry["baseline_digest_path"])
        record, body = _frontmatter(record_path)
        if record.get("content_hash") != expected_hash:
            raise CorpusValidationError(f"{slot} record hash does not match manifest")
        copyright_status = (record.get("copyright") or {}).get("status")
        if copyright_status != rights.get("status"):
            raise CorpusValidationError(f"{slot} rights status does not match record")

        review = json.loads(review_path.read_text())
        source_reviewed = (
            entry.get("source_review", {}).get("status") == "reviewed"
            and review.get("schema") == "anomalica/review-coverage/1"
            and review.get("observed_coverage") == 1.0
            and review.get("digestible") is True
        )
        if not source_reviewed:
            raise CorpusValidationError(f"{slot} lacks complete source review")

        digest = _load_mapping(digest_path)
        if (digest.get("record") or {}).get("content_hash") != expected_hash:
            raise CorpusValidationError(
                f"{slot} baseline digest targets another record"
            )

        gold_units = validate_claim_gold(entry, body, manifest)
        basis = rights.get("admission_basis")
        local_rights = basis == "public_domain" or (
            basis == LOCAL_ANALYSIS
            and rights.get("status") == "publicly_accessible"
            and rights.get("controlled_local_only") is True
        )
        permission_evidenced = _permission_evidence(entry, manifest) is not None
        local_rights = local_rights or (
            basis in {"open_licence", "internal_evaluation_permission"}
            and permission_evidenced
        )
        hosted_rights = basis == "public_domain" or (
            basis in {"open_licence", "internal_evaluation_permission"}
            and permission_evidenced
        )
        local_ready = source_reviewed and gold_units > 0 and local_rights
        hosted_ready = source_reviewed and gold_units > 0 and hosted_rights
        local_declared = entry.get("local_evaluation_readiness")
        hosted_declared = entry.get("hosted_evaluation_readiness")
        if local_declared != ("ready" if local_ready else "blocked"):
            raise CorpusValidationError(
                f"{slot} local readiness contradicts its evidence"
            )
        if hosted_declared != ("ready" if hosted_ready else "blocked"):
            raise CorpusValidationError(
                f"{slot} hosted readiness contradicts its evidence"
            )
        summaries.append(
            {
                "slot": slot,
                "record_content_hash": expected_hash,
                "source_review": "reviewed",
                "claim_gold": entry.get("claim_gold", {}).get("status"),
                "gold_units": gold_units,
                "local_evaluation_readiness": local_declared,
                "hosted_evaluation_readiness": hosted_declared,
            }
        )

    multilingual = (doc.get("coverage_slots") or {}).get("multilingual") or {}
    if multilingual.get("required") is not True or multilingual.get("status") not in {
        "ready",
        "blocked",
    }:
        raise CorpusValidationError("multilingual coverage slot is not explicit")
    if multilingual.get("status") == "ready" and not multilingual.get(
        "record_content_hash"
    ):
        raise CorpusValidationError("ready multilingual slot has no record")
    return {
        "schema": SCHEMA,
        "records": summaries,
        "local_ready_records": sum(
            item["local_evaluation_readiness"] == "ready" for item in summaries
        ),
        "hosted_ready_records": sum(
            item["hosted_evaluation_readiness"] == "ready" for item in summaries
        ),
        "multilingual": {
            "status": multilingual.get("status"),
            "record_content_hash": multilingual.get("record_content_hash"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        default=Path(__file__).with_name("evaluation-corpus.yaml"),
    )
    args = parser.parse_args()
    print(json.dumps(validate(args.manifest), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
