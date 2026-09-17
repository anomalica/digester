import copy
from pathlib import Path

import pytest
from click.testing import CliRunner

from digester.cli import main
from digester.fixture_eval import (
    ADJUDICATION_SCHEMA,
    FixtureError,
    file_sha256,
    load_document,
    load_predictions,
    score,
    validate_adjudications,
    validate_fixture,
)


ROOT = Path(__file__).resolve().parent.parent / "benchmarks/digestion-eval"
RIDGE_CASE = "ridge-rescue-reverse-narration"


@pytest.fixture
def fixture_document():
    return load_document(ROOT / "cases.yaml")


@pytest.fixture
def prediction():
    return load_document(ROOT / "predictions/ridge-rescue-reference.yaml")


@pytest.fixture
def prediction_set():
    fixture = load_document(ROOT / "cases.yaml")
    return load_predictions(
        ROOT / "reference-set.yaml", [case["id"] for case in fixture["cases"]]
    )


def _expectation(result, dimension, expectation):
    return next(
        item
        for case in result["cases"]
        for item in case["expectations"]
        if item["dimension"] == dimension and item["expectation"] == expectation
    )


def _adjudication(prediction_path, decision):
    return {
        "schema": ADJUDICATION_SCHEMA,
        "fixture_sha256": file_sha256(ROOT / "cases.yaml"),
        "prediction_sha256": {RIDGE_CASE: file_sha256(prediction_path)},
        "judge": {"kind": "model", "identifier": "example/semantic-judge-v1"},
        "metadata": {"experiment": "unit-test"},
        "decisions": [decision],
    }


def _validated_adjudication(document, prediction_path, prediction):
    fixture = load_document(ROOT / "cases.yaml")
    return validate_adjudications(
        document,
        fixture_sha256=file_sha256(ROOT / "cases.yaml"),
        prediction_sha256={RIDGE_CASE: file_sha256(prediction_path)},
        fixture=fixture,
        predictions={RIDGE_CASE: prediction},
    )


def test_fixture_schema_rejects_unknown_concepts(fixture_document):
    broken = copy.deepcopy(fixture_document)
    broken["cases"][0]["expectations"]["accounts"][0]["concepts"].append("missing")

    with pytest.raises(FixtureError, match="unknown concepts"):
        validate_fixture(broken)


def test_alternative_phrasing_and_date_range_pass(fixture_document, prediction_set):
    result = score(fixture_document, prediction_set)

    assert _expectation(result, "coverage", "rescue-start")["passed"]
    assert "started" in _expectation(result, "coverage", "rescue-start")["evidence"]
    assert _expectation(result, "dates", "restoration-range")["passed"]


def test_default_coverage_is_deterministic(fixture_document, prediction):
    result = score(
        fixture_document,
        {RIDGE_CASE: prediction},
        case_id=RIDGE_CASE,
    )

    coverage = _expectation(result, "coverage", "rescue-end")
    assert coverage["passed"]
    assert coverage["matches"] == [
        {"claim_id": "rescue-end", "source": "deterministic"}
    ]
    assert coverage["adjudications"] == []


def test_semantic_adjudication_accepts_paraphrase_for_downstream_edges(
    tmp_path, fixture_document, prediction
):
    paraphrased = copy.deepcopy(prediction)
    paraphrased["domain_claims"][0]["text"] = (
        "Mara Voss completed the evacuation of the injured climber to headquarters."
    )
    prediction_path = tmp_path / "paraphrased.yaml"
    import yaml

    prediction_path.write_text(yaml.safe_dump(paraphrased, sort_keys=False))
    sidecar = _adjudication(
        prediction_path,
        {
            "case": RIDGE_CASE,
            "concept": "rescue-end",
            "claim_ids": ["rescue-end"],
            "decision": "equivalent",
            "rationale": "The claim describes completion of the same rescue.",
        },
    )
    adjudication = _validated_adjudication(sidecar, prediction_path, paraphrased)

    result = score(
        fixture_document,
        {RIDGE_CASE: paraphrased},
        case_id=RIDGE_CASE,
        adjudications=adjudication,
    )

    coverage = _expectation(result, "coverage", "rescue-end")
    assert coverage["passed"]
    assert coverage["matches"] == [
        {"claim_id": "rescue-end", "source": "semantic_adjudication"}
    ]
    assert "adjudicated equivalent" in coverage["evidence"]
    assert _expectation(result, "accounts", "rescue-account")["passed"]
    assert _expectation(result, "chronology", "start-before-end")["passed"]

    sidecar_path = tmp_path / "paraphrased.adjudication.yaml"
    sidecar_path.write_text(yaml.safe_dump(sidecar, sort_keys=False))
    cli_result = CliRunner().invoke(
        main,
        [
            "eval-fixtures",
            str(ROOT / "cases.yaml"),
            "--case",
            RIDGE_CASE,
            "--variant",
            f"candidate={prediction_path}",
            "--adjudication",
            f"candidate={sidecar_path}",
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert "PASS coverage/rescue-end: adjudicated equivalent" in cli_result.output


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("fixture", "fixture hash is stale"),
        ("prediction", "prediction hashes are stale"),
    ],
)
def test_adjudication_with_stale_hash_fails_closed(
    tmp_path, prediction, target, message
):
    prediction_path = tmp_path / "prediction.yaml"
    import yaml

    prediction_path.write_text(yaml.safe_dump(prediction, sort_keys=False))
    sidecar = _adjudication(
        prediction_path,
        {
            "case": RIDGE_CASE,
            "concept": "rescue-end",
            "claim_ids": ["rescue-end"],
            "decision": "equivalent",
            "rationale": "Equivalent.",
        },
    )
    if target == "fixture":
        sidecar["fixture_sha256"] = "sha256:" + "0" * 64
    else:
        sidecar["prediction_sha256"][RIDGE_CASE] = "sha256:" + "0" * 64

    with pytest.raises(FixtureError, match=message):
        _validated_adjudication(sidecar, prediction_path, prediction)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("concept", "missing-concept", "unknown concept"),
        ("claim_ids", ["missing-claim"], "unknown claims"),
    ],
)
def test_adjudication_unknown_references_fail_closed(
    tmp_path, prediction, field, value, message
):
    prediction_path = tmp_path / "prediction.yaml"
    import yaml

    prediction_path.write_text(yaml.safe_dump(prediction, sort_keys=False))
    decision = {
        "case": RIDGE_CASE,
        "concept": "rescue-end",
        "claim_ids": ["rescue-end"],
        "decision": "equivalent",
        "rationale": "Equivalent.",
    }
    decision[field] = value

    with pytest.raises(FixtureError, match=message):
        _validated_adjudication(
            _adjudication(prediction_path, decision), prediction_path, prediction
        )


def test_adjudication_abstention_is_inspectable_and_does_not_pass(
    tmp_path, fixture_document, prediction
):
    paraphrased = copy.deepcopy(prediction)
    paraphrased["domain_claims"][0]["text"] = "The operation concluded successfully."
    prediction_path = tmp_path / "prediction.yaml"
    import yaml

    prediction_path.write_text(yaml.safe_dump(paraphrased, sort_keys=False))
    sidecar = _adjudication(
        prediction_path,
        {
            "case": RIDGE_CASE,
            "concept": "rescue-end",
            "claim_ids": ["rescue-end"],
            "decision": "abstain",
            "evidence": "The claim omits the climber and destination.",
        },
    )

    result = score(
        fixture_document,
        {RIDGE_CASE: paraphrased},
        case_id=RIDGE_CASE,
        adjudications=_validated_adjudication(sidecar, prediction_path, paraphrased),
    )

    coverage = _expectation(result, "coverage", "rescue-end")
    assert not coverage["passed"]
    assert coverage["adjudications"][0]["decision"] == "abstain"
    assert "adjudicated abstain" in coverage["evidence"]


def test_grouped_pronoun_context_and_reverse_chronology_pass(
    fixture_document, prediction
):
    result = score(
        fixture_document,
        {"ridge-rescue-reverse-narration": prediction},
        case_id="ridge-rescue-reverse-narration",
    )

    assert _expectation(result, "context", "pronoun-resolves-in-rescue-account")[
        "passed"
    ]
    assert _expectation(result, "chronology", "start-before-end")["passed"]
    assert _expectation(result, "chronology", "failures-simultaneous")["passed"]
    assert _expectation(result, "chronology", "rockfall-order-unknown")["passed"]


@pytest.mark.parametrize(
    ("mutation", "dimension", "expectation"),
    [
        (lambda doc: doc["domain_claims"][1].pop("date"), "dates", "start-date"),
        (
            lambda doc: doc["domain_claims"][0].update(refs=[]),
            "references",
            "rescue-event-reference",
        ),
        (
            lambda doc: (
                doc["evaluation"]["accounts"][0]["claim_ids"].remove("rescue-end"),
                doc["evaluation"].update(chronology=[]),
            ),
            "accounts",
            "rescue-account",
        ),
        (
            lambda doc: doc["evaluation"]["chronology"][0].update(
                before=[["rescue-end", "rescue-start"]]
            ),
            "chronology",
            "start-before-end",
        ),
    ],
)
def test_missing_date_reference_group_and_edge_fail(
    fixture_document, prediction, mutation, dimension, expectation
):
    broken = copy.deepcopy(prediction)
    mutation(broken)

    result = score(
        fixture_document,
        {"ridge-rescue-reverse-narration": broken},
        case_id="ridge-rescue-reverse-narration",
    )

    assert not _expectation(result, dimension, expectation)["passed"]


def test_cli_compares_named_variants_with_readable_evidence(
    tmp_path, fixture_document, prediction
):
    degraded = copy.deepcopy(prediction)
    degraded["domain_claims"][0]["text"] = "An unrelated claim."
    degraded_path = tmp_path / "degraded.yaml"
    import yaml

    degraded_path.write_text(yaml.safe_dump(degraded, sort_keys=False))
    result = CliRunner().invoke(
        main,
        [
            "eval-fixtures",
            str(ROOT / "cases.yaml"),
            "--case",
            "ridge-rescue-reverse-narration",
            "--variant",
            f"reference={ROOT / 'predictions/ridge-rescue-reference.yaml'}",
            "--variant",
            f"degraded={degraded_path}",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Variant: reference" in result.output
    assert "Variant: degraded" in result.output
    assert "PASS chronology/start-before-end" in result.output
    assert "FAIL coverage/rescue-end" in result.output
    assert "semantic matches are explicitly labelled adjudicated" in result.output
