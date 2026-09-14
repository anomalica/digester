import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

from evaluation_corpus import (  # noqa: E402
    CorpusValidationError,
    MISSING_REFERENCE_REASON,
    SCHEMA,
    authorise_dispatch,
    validate,
    validate_claim_gold,
)
import run_models  # noqa: E402


MANIFEST = Path(__file__).resolve().parent.parent / "benchmarks/evaluation-corpus.yaml"


def _dispatch_fixture(
    tmp_path,
    *,
    basis="local_information_analysis",
    status="publicly_accessible",
    coverage=1.0,
    digestible=True,
    gold=True,
):
    record_hash = "sha256:" + "a" * 64
    body = "{{highlight-start: h1}}expected fact{{highlight-end: h1}}"
    record = tmp_path / "record.md"
    record.write_text(
        f"---\ncontent_hash: {record_hash}\ncopyright:\n  status: {status}\n---\n{body}"
    )
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "schema": "anomalica/review-coverage/1",
                "observed_coverage": coverage,
                "digestible": digestible,
            }
        )
    )
    sidecar = tmp_path / "gold.json"
    sidecar.write_text(
        json.dumps(
            {
                "schema": "anomalica/highlight-gold/1",
                "record_hash": record_hash,
                "body_sha256": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                "ranges": [
                    {
                        "id": "r1",
                        "start": 0,
                        "end": len(body),
                        "complete": coverage == 1.0,
                        "reviewer": {
                            "issuer": "test",
                            "subject": "1",
                            "name": "Reviewer",
                        },
                        "updated_at": "2026-09-13T00:00:00Z",
                        **(
                            {"attested_at": "2026-09-13T00:00:00Z"}
                            if coverage == 1.0
                            else {}
                        ),
                        "units": [
                            {
                                "highlight_id": "h1",
                                "decision": "accept",
                                "facts": ["Expected fact."],
                            }
                        ],
                    }
                ],
            }
        )
    )
    rights = {
        "status": status,
        "controlled_local_only": basis == "local_information_analysis",
    }
    if basis is not None:
        rights["admission_basis"] = basis
    claim_gold = {
        "status": "human-reviewed" if gold else "missing",
        "mechanism": "highlight-gold",
        "path": sidecar.name if gold else None,
    }
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema": SCHEMA,
                "records": [
                    {
                        "slot": "test-record",
                        "record_content_hash": record_hash,
                        "rights": rights,
                        "review_path": review.name,
                        "claim_gold": claim_gold,
                    }
                ],
            }
        )
    )
    return manifest, record


def test_real_corpus_evidence_is_consistent_and_honestly_blocked():
    manifest = yaml.safe_load(MANIFEST.read_text())
    first_record = (MANIFEST.parent / manifest["records"][0]["record_path"]).resolve()
    if not first_record.exists():
        pytest.skip(
            "real corpus lives in sibling repositories not mounted in this test"
        )
    result = validate(MANIFEST)
    assert result["schema"] == SCHEMA
    assert [entry["slot"] for entry in result["records"]] == [
        "straightforward_article",
        "difficult_long_document",
        "dialogue_audio",
    ]
    assert all(entry["source_review"] == "reviewed" for entry in result["records"])
    assert result["local_ready_records"] == 0
    assert result["hosted_ready_records"] == 0
    state = result["state"]
    assert state["schema"] == "anomalica/evaluation-state/1"
    assert state["evaluation_id"] == "digest-evaluation-corpus"
    assert state["status"] == "blocked"
    assert state["blocked_reason"] == MISSING_REFERENCE_REASON
    assert state["gold"] == {
        "status": "unavailable",
        "reviewed": 0,
        "total": 0,
        "unit": "reference-highlight",
    }
    assert state["decision"] == {
        "code": "await-reference-highlights",
        "summary": "Await authenticated human reference highlights.",
    }
    states = state["items"]
    assert all(item["status"] == "blocked" for item in states)
    assert all(item["blocked_reason"] == MISSING_REFERENCE_REASON for item in states)
    assert [item["id"] for item in states] == [
        "straightforward_article",
        "difficult_long_document",
        "dialogue_audio",
    ]
    assert [item["review_id"] for item in states] == [
        "digest-claim-gold:straightforward_article",
        "digest-claim-gold:difficult_long_document",
        "digest-claim-gold:dialogue_audio",
    ]
    assert all(item["action"]["record_id"] == item["record_id"] for item in states)
    assert all(item["source_review"] == {"status": "reviewed"} for item in states)
    assert all(item["gold"]["reviewed"] == 0 for item in states)
    assert all(item["digest_sha256"].startswith("sha256:") for item in states)
    assert all(item["action"]["route"] == "record-highlights" for item in states)
    assert all(item["action"]["starts_from_zero"] is True for item in states)
    assert all(
        item["action"]["model_drafted_suggestions"] == "optional-provisional"
        for item in states
    )
    assert all("title" not in item and "path" not in item for item in states)
    assert state["evidence"][0]["artifact_id"] == "digest-evaluation-corpus-manifest"
    assert all(item["sha256"].startswith("sha256:") for item in state["evidence"])
    expected_evidence_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                state["evidence"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    assert state["evidence_sha256"] == expected_evidence_hash
    assert result["multilingual"] == {
        "status": "blocked",
        "record_content_hash": None,
    }


def test_state_only_cli_emits_compact_private_adapter_payload():
    manifest = yaml.safe_load(MANIFEST.read_text())
    first_record = (MANIFEST.parent / manifest["records"][0]["record_path"]).resolve()
    if not first_record.exists():
        pytest.skip(
            "real corpus lives in sibling repositories not mounted in this test"
        )

    completed = subprocess.run(
        [
            sys.executable,
            str(MANIFEST.with_name("evaluation_corpus.py")),
            str(MANIFEST),
            "--state-only",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=MANIFEST.parents[2],
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    state = json.loads(completed.stdout)

    assert state["schema"] == "anomalica/evaluation-state/1"
    assert state["evaluation_id"] == "digest-evaluation-corpus"
    assert "records" not in state


def test_provisional_external_spans_cannot_masquerade_as_human_gold(tmp_path):
    source = yaml.safe_load(MANIFEST.read_text())
    entry = copy.deepcopy(source["records"][2])
    provisional = {
        "schema": "anomalica/highlights/1",
        "purpose": "digest-evaluation-claim-gold",
        "provisional": True,
        "drafted_by": "model-draft",
        "record_hash": entry["record_content_hash"],
        "body_sha256": hashlib.sha256(b"verbatim source").hexdigest(),
        "complete": False,
        "reviewed_by": "person@example.test",
        "reviewed_at": "2026-09-13T00:00:00Z",
        "spans": [{"start": 0, "end": 15, "text": "verbatim source"}],
        "rejected": [],
    }
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps(provisional))
    manifest = tmp_path / "manifest.yaml"
    entry["claim_gold"] = {
        "status": "human-reviewed",
        "mechanism": "external-spans",
        "path": gold.name,
    }
    with pytest.raises(CorpusValidationError, match="unsupported human claim-gold"):
        validate_claim_gold(entry, "verbatim source", manifest)


def test_authenticated_external_highlights_are_admitted(tmp_path):
    body = "{{highlight-start: h1}}expected fact{{highlight-end: h1}} and {{highlight-start: h2}}rejected candidate{{highlight-end: h2}}"
    entry = {
        "slot": "external-record",
        "record_content_hash": "sha256:" + "a" * 64,
        "claim_gold": {
            "status": "human-reviewed",
            "mechanism": "highlight-gold",
            "path": "gold.json",
        },
    }
    sidecar = {
        "schema": "anomalica/highlight-gold/1",
        "record_hash": "sha256:" + "a" * 64,
        "body_sha256": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        "ranges": [
            {
                "id": "r1",
                "start": 0,
                "end": len(body),
                "complete": True,
                "reviewer": {"issuer": "test", "subject": "1", "name": "Reviewer"},
                "updated_at": "2026-09-13T00:00:00Z",
                "attested_at": "2026-09-13T00:00:00Z",
                "units": [
                    {
                        "highlight_id": "h1",
                        "decision": "accept",
                        "facts": ["Expected fact."],
                    },
                    {"highlight_id": "h2", "decision": "reject"},
                ],
            }
        ],
    }
    (tmp_path / "gold.json").write_text(json.dumps(sidecar))

    assert validate_claim_gold(entry, body, tmp_path / "manifest.yaml") == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"body_sha256": "sha256:" + "0" * 64}, "body hash"),
        ({"record_hash": "sha256:" + "b" * 64}, "another record"),
        ({"schema": "anomalica/highlight-gold/2"}, "unsupported"),
    ],
)
def test_external_highlights_reject_mismatched_evidence(tmp_path, change, message):
    body = "{{highlight-start: h1}}expected fact{{highlight-end: h1}}"
    entry = {
        "slot": "external-record",
        "record_content_hash": "sha256:" + "a" * 64,
        "claim_gold": {
            "status": "human-reviewed",
            "mechanism": "highlight-gold",
            "path": "gold.json",
        },
    }
    sidecar = {
        "schema": "anomalica/highlight-gold/1",
        "record_hash": "sha256:" + "a" * 64,
        "body_sha256": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        "ranges": [
            {
                "id": "r1",
                "start": 0,
                "end": len(body),
                "complete": True,
                "reviewer": {"issuer": "test", "subject": "1", "name": "Reviewer"},
                "updated_at": "2026-09-13T00:00:00Z",
                "attested_at": "2026-09-13T00:00:00Z",
                "units": [
                    {
                        "highlight_id": "h1",
                        "decision": "accept",
                        "facts": ["Expected fact."],
                    }
                ],
            }
        ],
    }
    sidecar.update(change)
    (tmp_path / "gold.json").write_text(json.dumps(sidecar))

    with pytest.raises(CorpusValidationError, match=message):
        validate_claim_gold(entry, body, tmp_path / "manifest.yaml")


def test_highlight_gold_ranges_must_not_overlap(tmp_path):
    body = "{{highlight-start: h1}}expected fact{{highlight-end: h1}}"
    entry = {
        "slot": "external-record",
        "record_content_hash": "sha256:" + "a" * 64,
        "claim_gold": {
            "status": "human-reviewed",
            "mechanism": "highlight-gold",
            "path": "gold.json",
        },
    }
    sidecar = {
        "schema": "anomalica/highlight-gold/1",
        "record_hash": "sha256:" + "a" * 64,
        "body_sha256": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        "ranges": [
            {
                "id": "r1",
                "start": 0,
                "end": len(body),
                "complete": False,
                "reviewer": {"issuer": "test", "subject": "1", "name": "Reviewer"},
                "updated_at": "2026-09-13T00:00:00Z",
                "units": [],
            },
            {
                "id": "r2",
                "start": 1,
                "end": len(body),
                "complete": False,
                "reviewer": {"issuer": "test", "subject": "2", "name": "Reviewer 2"},
                "updated_at": "2026-09-13T00:00:00Z",
                "units": [],
            },
        ],
    }
    (tmp_path / "gold.json").write_text(json.dumps(sidecar))

    with pytest.raises(CorpusValidationError, match="ranges overlap"):
        validate_claim_gold(entry, body, tmp_path / "manifest.yaml")


def test_in_body_highlights_are_non_gold_migration_candidates(tmp_path):
    entry = {
        "slot": "future-internal-record",
        "claim_gold": {
            "status": "migration-candidate",
            "mechanism": "in-body-highlights",
            "path": None,
        },
    }
    body = "before {{highlight-start: a}}expected fact{{highlight-end: a}} after"
    assert validate_claim_gold(entry, body, tmp_path / "manifest.yaml") == 0


def test_in_body_highlights_cannot_masquerade_as_authenticated_gold(tmp_path):
    entry = {
        "slot": "future-internal-record",
        "claim_gold": {
            "status": "human-reviewed",
            "mechanism": "in-body-highlights",
            "path": None,
        },
    }
    body = "before {{highlight-start: a}}expected fact{{highlight-end: a}} after"
    with pytest.raises(CorpusValidationError, match="unsupported human claim-gold"):
        validate_claim_gold(entry, body, tmp_path / "manifest.yaml")


def test_controlled_local_information_analysis_is_admitted_locally(tmp_path):
    manifest, record = _dispatch_fixture(tmp_path)

    admission = authorise_dispatch(manifest, record, use="local-deterministic-grading")

    assert admission["scope"] == "whole-record"
    assert admission["gold_units"] == 1


def test_local_information_analysis_is_denied_to_hosted_routes(tmp_path):
    manifest, record = _dispatch_fixture(tmp_path)

    with pytest.raises(CorpusValidationError, match="does not permit hosted"):
        authorise_dispatch(
            manifest,
            record,
            use="hosted-model-inference",
            provider="openrouter",
            route="openrouter",
        )


def test_partial_review_is_local_partial_unit_only_and_denied_hosted(tmp_path):
    manifest, record = _dispatch_fixture(
        tmp_path, coverage=0.25185008355216043, digestible=False
    )

    local = authorise_dispatch(manifest, record, use="local-deterministic-grading")
    assert local["scope"] == "partial-units-only"

    with pytest.raises(CorpusValidationError, match="complete source review"):
        authorise_dispatch(
            manifest,
            record,
            use="hosted-model-inference",
            provider="openrouter",
            route="openrouter",
        )


def test_missing_rights_basis_fails_closed(tmp_path):
    manifest, record = _dispatch_fixture(tmp_path, basis=None)

    with pytest.raises(CorpusValidationError, match="no evaluation rights basis"):
        authorise_dispatch(manifest, record, use="local-deterministic-grading")


def test_internal_evaluation_permission_must_have_evidence(tmp_path):
    manifest, record = _dispatch_fixture(
        tmp_path,
        basis="internal_evaluation_permission",
        status="publicly_accessible",
    )

    with pytest.raises(CorpusValidationError, match="permission evidence"):
        authorise_dispatch(manifest, record, use="local-deterministic-grading")


def test_public_domain_record_with_complete_evidence_is_admitted_hosted(tmp_path):
    manifest, record = _dispatch_fixture(
        tmp_path, basis="public_domain", status="public_domain"
    )

    admission = authorise_dispatch(
        manifest,
        record,
        use="hosted-model-inference",
        provider="openrouter",
        route="openrouter",
    )

    assert admission["provider"] == "openrouter"
    assert admission["scope"] == "whole-record"


def test_run_models_checks_manifest_before_reading_credentials(tmp_path, monkeypatch):
    manifest, record = _dispatch_fixture(tmp_path, gold=False)
    output = tmp_path / "outputs"
    monkeypatch.setattr(run_models, "CORPUS_MANIFEST", manifest)
    monkeypatch.setattr(run_models, "OUT_DIR", output)
    monkeypatch.setattr(run_models, "resolve_record", lambda: record)

    def unexpected_credentials():
        raise AssertionError("credentials were read before corpus admission")

    monkeypatch.setattr(run_models, "openrouter_key", unexpected_credentials)
    with pytest.raises(SystemExit, match="record has no authenticated claim gold"):
        run_models.main()
    assert not output.exists()
