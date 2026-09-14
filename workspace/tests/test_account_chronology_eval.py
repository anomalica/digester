import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

from account_chronology_eval import (  # noqa: E402
    PRIVATE_BENCHMARK_ROOT,
    EvaluationError,
    _confined_path,
    audit_manifest,
    materialise_claims_from_digest,
    score,
    validate_document,
)


RECORD_HASH = "sha256:" + "a" * 64
PRE_DIGEST_HASH = "b" * 64
DIGEST_HASH = "c" * 64
MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "benchmarks/account-chronology-evaluation.yaml"
)


def _claim(claim_id, position):
    return {
        "id": claim_id,
        "record_content_hash": RECORD_HASH,
        "position": position,
    }


def _assignment(claim_id, account_id=None, status="bound"):
    return {
        "claim_id": claim_id,
        "account_id": account_id,
        "status": status,
    }


def _documents():
    claims = [
        _claim("c1", 10),
        _claim("c2", 25),
        _claim("c3", 210),
        _claim("c4", 410),
        _claim("c7", 250),
        _claim("c8", 450),
        _claim("c9", 270),
        _claim("c10", 280),
        _claim("outside", 350),
        _claim("lost", None),
    ]
    gold = {
        "schema": "anomalica/account-chronology-evaluation/1",
        "record_content_hash": RECORD_HASH,
        "pre_digest_sha256": PRE_DIGEST_HASH,
        "digest_sha256": DIGEST_HASH,
        "coordinate_system": "media_time_ms",
        "reviewed_by": "reviewer@example.test",
        "reviewed_at": "2026-09-14T00:00:00Z",
        "accounts": [
            {
                "id": "outer",
                "spans": [{"start": 0, "end": 100}, {"start": 200, "end": 300}],
            },
            {
                "id": "inner",
                "parent_account_id": "outer",
                "spans": [{"start": 20, "end": 40}],
            },
            {"id": "other", "spans": [{"start": 400, "end": 500}]},
        ],
        "claims": copy.deepcopy(claims),
        "claim_assignments": [
            _assignment("c1", "outer"),
            _assignment("c2", "inner"),
            _assignment("c3", "outer"),
            _assignment("c4", "other"),
            _assignment("c7", "outer"),
            _assignment("c8", "other"),
            _assignment("c9", "outer"),
            _assignment("c10", "outer"),
            _assignment("outside", status="outside"),
            _assignment("lost", status="unlocatable"),
        ],
        "before_pairs": [
            {
                "account_id": "outer",
                "before_claim_id": "c1",
                "after_claim_id": "c3",
            },
            {
                "account_id": "outer",
                "before_claim_id": "c3",
                "after_claim_id": "c7",
            },
            {
                "account_id": "other",
                "before_claim_id": "c4",
                "after_claim_id": "c8",
            },
        ],
        "unknown_pairs": [{"account_id": "outer", "claim_ids": ["c7", "c9"]}],
        "simultaneous_pairs": [{"account_id": "outer", "claim_ids": ["c9", "c10"]}],
    }
    predicted = {
        "schema": "anomalica/account-chronology-evaluation/1",
        "record_content_hash": RECORD_HASH,
        "pre_digest_sha256": PRE_DIGEST_HASH,
        "digest_sha256": DIGEST_HASH,
        "coordinate_system": "media_time_ms",
        "accounts": [
            {
                "id": "p-outer",
                "spans": [{"start": 0, "end": 95}, {"start": 205, "end": 300}],
            },
            {
                "id": "p-inner",
                "parent_account_id": "p-outer",
                "spans": [{"start": 20, "end": 40}],
            },
            {"id": "p-other", "spans": [{"start": 400, "end": 500}]},
            {"id": "extra", "spans": [{"start": 600, "end": 650}]},
        ],
        "claims": claims,
        "claim_assignments": [
            _assignment("c1", "p-outer"),
            _assignment("c2", "p-inner"),
            _assignment("c3", "p-outer"),
            _assignment("c4", "p-other"),
            _assignment("c7", "p-outer"),
            _assignment("c8", "p-other"),
            _assignment("c9", "p-outer"),
            _assignment("c10", "p-outer"),
            _assignment("outside", status="outside"),
            _assignment("lost", status="unlocatable"),
        ],
        "before_pairs": [
            {
                "account_id": "p-outer",
                "before_claim_id": "c1",
                "after_claim_id": "c3",
            },
            {
                "account_id": "p-other",
                "before_claim_id": "c8",
                "after_claim_id": "c4",
            },
            {
                "account_id": "p-outer",
                "before_claim_id": "c7",
                "after_claim_id": "c9",
            },
            {
                "account_id": "p-outer",
                "before_claim_id": "c9",
                "after_claim_id": "c10",
            },
        ],
    }
    return gold, predicted


def test_scores_accounts_exact_bindings_and_closed_before_pairs():
    gold, predicted = _documents()

    result = score(gold, predicted)

    assert result["accounts"]["matched"] == 3
    assert result["accounts"]["precision"] == 0.75
    assert result["accounts"]["recall"] == 1.0
    assert 0.9 < result["accounts"]["mean_matched_span_iou"] <= 1.0
    assert result["claim_assignments"] == {
        "correct": 10,
        "wrong_account": 0,
        "abstained": 0,
    }
    assert result["chronology"] == {
        "correct": 1,
        "wrong_direction": 1,
        "abstained": 2,
        "unknown_assertions": 1,
        "simultaneous_errors": 1,
        "unsupported_assertions": 1,
        "unmatched_account_assertions": 0,
    }


def test_nested_account_must_win_exact_binding():
    gold, _ = _documents()
    nested = next(a for a in gold["claim_assignments"] if a["claim_id"] == "c2")
    nested["account_id"] = "outer"

    with pytest.raises(EvaluationError, match="bypasses a nested account"):
        validate_document(gold, gold=True)


def test_interrupted_account_accepts_claims_in_each_span():
    gold, _ = _documents()

    validated = validate_document(gold, gold=True)

    assert validated["assignments"]["c1"] == "outer"
    assert validated["assignments"]["c3"] == "outer"


@pytest.mark.parametrize(
    ("claim_id", "message"), [("outside", "outside"), ("lost", "forced")]
)
def test_outside_and_unlocatable_claims_cannot_be_forced(claim_id, message):
    gold, _ = _documents()
    assignment = next(
        item for item in gold["claim_assignments"] if item["claim_id"] == claim_id
    )
    assignment.update({"status": "bound", "account_id": "outer"})

    with pytest.raises(EvaluationError, match=message):
        validate_document(gold, gold=True)


def test_temporal_pair_cannot_cross_accounts():
    gold, _ = _documents()
    gold["before_pairs"][0]["after_claim_id"] = "c4"

    with pytest.raises(EvaluationError, match="crosses an account boundary"):
        validate_document(gold, gold=True)


def test_claim_and_temporal_evidence_cannot_cross_records():
    gold, _ = _documents()
    gold["claims"][0]["record_content_hash"] = "sha256:" + "b" * 64

    with pytest.raises(EvaluationError, match="crosses a record boundary"):
        validate_document(gold, gold=True)


def test_before_cycles_are_rejected_after_closure():
    gold, _ = _documents()
    gold["before_pairs"].append(
        {
            "account_id": "outer",
            "before_claim_id": "c7",
            "after_claim_id": "c1",
        }
    )

    with pytest.raises(EvaluationError, match="cycle"):
        validate_document(gold, gold=True)


def test_known_unknown_and_simultaneous_labels_cannot_contradict():
    gold, _ = _documents()
    gold["unknown_pairs"].append({"account_id": "outer", "claim_ids": ["c1", "c3"]})

    with pytest.raises(EvaluationError, match="contradict"):
        validate_document(gold, gold=True)


def test_account_matching_is_globally_optimal_not_greedy():
    common = {
        "schema": "anomalica/account-chronology-evaluation/1",
        "record_content_hash": RECORD_HASH,
        "pre_digest_sha256": PRE_DIGEST_HASH,
        "digest_sha256": DIGEST_HASH,
        "coordinate_system": "media_time_ms",
        "claims": [],
        "claim_assignments": [],
    }
    gold = {
        **common,
        "reviewed_by": "reviewer@example.test",
        "reviewed_at": "2026-09-14T00:00:00Z",
        "accounts": [
            {"id": "g-short", "spans": [{"start": 0, "end": 10}]},
            {"id": "g-long", "spans": [{"start": 0, "end": 20}]},
        ],
    }
    predicted = {
        **common,
        "accounts": [
            {"id": "p-short", "spans": [{"start": 0, "end": 20}]},
            {"id": "p-long", "spans": [{"start": 0, "end": 30}]},
        ],
    }

    assert score(gold, predicted)["accounts"]["matched"] == 2


def test_nested_parent_cycles_are_rejected():
    gold, _ = _documents()
    outer = next(account for account in gold["accounts"] if account["id"] == "outer")
    inner = next(account for account in gold["accounts"] if account["id"] == "inner")
    inner["spans"] = copy.deepcopy(outer["spans"])
    outer["parent_account_id"] = "inner"

    with pytest.raises(EvaluationError, match="cycle"):
        validate_document(gold, gold=True)


def test_prediction_cannot_change_claim_positions_or_input_hashes():
    gold, predicted = _documents()
    predicted["claims"][0]["position"] = 11

    with pytest.raises(EvaluationError, match="different claims"):
        score(gold, predicted)

    gold, predicted = _documents()
    predicted["digest_sha256"] = "d" * 64
    with pytest.raises(EvaluationError, match="different digests"):
        score(gold, predicted)

    gold, predicted = _documents()
    predicted["claims"] = list(reversed(predicted["claims"]))
    with pytest.raises(EvaluationError, match="different claims"):
        score(gold, predicted)


def test_claim_materialisation_uses_exact_location_start_milliseconds(tmp_path):
    digest_path = tmp_path / "digest.yaml"
    digest_path.write_text(
        yaml.safe_dump(
            {
                "record": {"content_hash": RECORD_HASH},
                "domain_claims": [
                    {"id": "domain", "location": "01:02:03.125-01:02:04.0"}
                ],
                "infrastructure_claims": [
                    {"id": "missing"},
                    {"id": "malformed", "location": "quoted source text"},
                ],
            }
        )
    )

    assert materialise_claims_from_digest(digest_path) == [
        {
            "id": "domain",
            "record_content_hash": RECORD_HASH,
            "position": 3_723_125,
        },
        {
            "id": "missing",
            "record_content_hash": RECORD_HASH,
            "position": None,
        },
        {
            "id": "malformed",
            "record_content_hash": RECORD_HASH,
            "position": None,
        },
    ]


def test_private_output_paths_cannot_escape_benchmark_root(tmp_path):
    base = tmp_path / "workspace/benchmarks"
    base.mkdir(parents=True)

    assert _confined_path(
        base,
        "private/account-chronology/doty/gold.yaml",
        PRIVATE_BENCHMARK_ROOT,
        "authenticated_gold.path",
    ).is_relative_to((base / "private").resolve())
    with pytest.raises(EvaluationError, match="escapes"):
        _confined_path(
            base,
            "../../reports/accounts/gold.yaml",
            PRIVATE_BENCHMARK_ROOT,
            "authenticated_gold.path",
        )
    with pytest.raises(EvaluationError, match="relative path"):
        _confined_path(
            base,
            "/tmp/gold.yaml",
            PRIVATE_BENCHMARK_ROOT,
            "authenticated_gold.path",
        )


def test_claim_materialisation_rejects_submillisecond_locations(tmp_path):
    digest_path = tmp_path / "digest.yaml"
    digest_path.write_text(
        yaml.safe_dump(
            {
                "record": {"content_hash": RECORD_HASH},
                "domain_claims": [
                    {"id": "claim", "location": "00:00:00.0001-00:00:01.0"}
                ],
            }
        )
    )

    with pytest.raises(EvaluationError, match="exact millisecond"):
        materialise_claims_from_digest(digest_path)


def test_binding_to_an_unmatched_account_is_wrong_not_correct_unbound():
    gold, predicted = _documents()
    predicted["accounts"].append(
        {"id": "fabricated", "spans": [{"start": 340, "end": 360}]}
    )
    assignment = next(
        item for item in predicted["claim_assignments"] if item["claim_id"] == "outside"
    )
    assignment.update({"status": "bound", "account_id": "fabricated"})

    result = score(gold, predicted)

    assert result["claim_assignments"]["wrong_account"] == 1
    assert result["claim_assignments"]["correct"] == 9


def test_exact_assignment_scoring_distinguishes_abstention_from_wrong_account():
    gold, predicted = _documents()
    predicted["accounts"] = [
        account for account in predicted["accounts"] if account["id"] != "p-other"
    ]
    for assignment in predicted["claim_assignments"]:
        if assignment["claim_id"] in {"c4", "c8"}:
            assignment.update({"account_id": None, "status": "outside"})
    predicted["before_pairs"] = [
        pair for pair in predicted["before_pairs"] if pair["account_id"] != "p-other"
    ]

    result = score(gold, predicted)

    assert result["claim_assignments"]["abstained"] == 2
    assert result["claim_assignments"]["wrong_account"] == 0


def test_legacy_doty_evidence_is_audited_but_not_claimed_as_scoreable():
    if not (
        MANIFEST.parent.parent.parent / "reports/accounts/doty.sonnet.yaml"
    ).exists():
        pytest.skip("report-only Doty artefacts are outside the container mount")

    result = audit_manifest(MANIFEST)

    assert result["records"][0]["status"] == "legacy-evidence-not-scoreable"
    assert result["records"][0]["materialised_claims"] == 394
    assert result["records"][0]["unlocatable_claims"] == 6
    assert result["records"][0]["scoreable_prediction_status"] == "blocked"
    assert result["records"][0]["gold_accounts"] == 39
    assert result["records"][0]["predicted_accounts"] == 20
    assert result["records"][0]["maximum_account_recall_from_count"] == 20 / 39
    assert result["records"][0]["legacy_binding_counts"] == {
        "bound": 280,
        "unbindable": 5,
        "outside": 109,
    }
