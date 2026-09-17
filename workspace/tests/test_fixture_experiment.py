import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from digester.cli import main
from digester.fixture_eval import DIMENSIONS, file_sha256
from digester.fixture_experiment import (
    ExperimentError,
    accept_quality_baseline,
    compare_scores,
    format_summary,
    load_baseline,
    run_experiment,
)


ROOT = Path(__file__).resolve().parent.parent / "benchmarks/digestion-eval"
FIXTURE = ROOT / "cases.yaml"
STUB_BASELINE = ROOT / "stub-baseline.json"
QUALITY_BASELINE = ROOT / "quality-baseline.json"
RESPONSES = ROOT / "stub-responses.yaml"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _stable_code_revision(monkeypatch):
    from digester import fixture_experiment

    monkeypatch.setattr(
        fixture_experiment,
        "code_revision",
        lambda repo: {"commit": "test-revision", "dirty": False},
    )


def _gate(prepared):
    return {item["id"]: object() for item in prepared}


def _dimensions(rate=0.5):
    return {
        name: {"passed": int(rate * 10), "total": 10, "rate": rate}
        for name in DIMENSIONS
    }


@pytest.mark.parametrize(
    ("changes", "outcome"),
    [
        ({}, "unchanged"),
        ({"coverage": 0.6}, "better"),
        ({"coverage": 0.4}, "worse"),
        ({"coverage": 0.6, "dates": 0.4}, "mixed"),
    ],
)
def test_baseline_comparison_names_better_worse_unchanged_and_mixed(changes, outcome):
    baseline = {"dimensions": _dimensions()}
    current_dimensions = _dimensions()
    for name, rate in changes.items():
        current_dimensions[name] = {
            "passed": int(rate * 10),
            "total": 10,
            "rate": rate,
        }

    comparison = compare_scores({"dimensions": current_dimensions}, baseline)

    assert comparison["outcome"] == outcome
    assert format_summary(comparison, []).startswith(f"{outcome}:")


def test_stub_experiment_runs_production_passes_and_writes_reproducible_metadata(
    tmp_path, monkeypatch
):
    from digester import extract

    provider_calls = []

    def provider_call(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError("provider transport must not run in stub mode")

    monkeypatch.setattr(extract, "_transport_call_with_document", provider_call)
    result = run_experiment(
        FIXTURE,
        STUB_BASELINE,
        tmp_path,
        model="sonnet",
        use_api=False,
        dispatch_gate=_gate,
        stub_responses=RESPONSES,
        run_id="stub-run",
        repo_root=REPO,
    )

    report = result["report"]
    assert provider_calls == []
    assert report["status"] == "complete"
    assert report["baseline_comparison"]["outcome"] == "unchanged"
    assert report["fixture"]["sha256"] == file_sha256(FIXTURE)
    assert report["model"] == "sonnet"
    assert (
        report["effective_configuration"]["post_processing"]["entailment"]["enabled"]
        is False
    )
    assert len(report["stub_calls"]) == 4
    assert {(item["case"], item["pass"]) for item in report["stub_calls"]} == {
        ("ridge-rescue-reverse-narration", "nodes"),
        ("ridge-rescue-reverse-narration", "claims"),
        ("archive-restoration-range", "nodes"),
        ("archive-restoration-range", "claims"),
    }
    assert report["extraction_config"].startswith("sha256:")
    assert report["code_revision"]["commit"]
    for prediction in report["predictions"].values():
        assert "/variants/" in f"/{prediction['path']}"
        assert prediction["sha256"].startswith("sha256:")
        assert (result["run_dir"] / prediction["path"]).exists()
    assert not list((result["run_dir"] / "digests").glob("fixture-*.yaml"))
    assert (result["run_dir"] / "summary.txt").read_text().startswith("unchanged:")


def test_experiment_failure_writes_failure_artifact(tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic extraction failure")

    with pytest.raises(RuntimeError, match="synthetic extraction failure"):
        run_experiment(
            FIXTURE,
            STUB_BASELINE,
            tmp_path,
            model="sonnet",
            use_api=False,
            dispatch_gate=_gate,
            stub_responses=RESPONSES,
            run_id="failed-run",
            extractor=fail,
            repo_root=REPO,
        )

    failure = json.loads((tmp_path / "runs/failed-run/failure.json").read_text())
    assert failure["status"] == "failed"
    assert failure["failed_case"] == "ridge-rescue-reverse-narration"
    assert "synthetic extraction failure" in failure["error"]


def test_experiment_records_semantic_adjudication_metadata(tmp_path):
    sources = {
        "ridge-rescue-reverse-narration": ROOT
        / "predictions/ridge-rescue-reference.yaml",
        "archive-restoration-range": ROOT
        / "predictions/archive-restoration-reference.yaml",
    }
    prediction_hashes = {
        case_id: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for case_id, path in sources.items()
    }
    sidecar = {
        "schema": "anomalica/digestion-evaluation-adjudications/1",
        "fixture_sha256": file_sha256(FIXTURE),
        "prediction_sha256": prediction_hashes,
        "judge": {"kind": "human", "identifier": "fixture-reviewer"},
        "metadata": {"experiment": "accepted-paraphrases"},
        "decisions": [],
    }
    sidecar_path = tmp_path / "adjudication.yaml"
    sidecar_path.write_text(yaml.safe_dump(sidecar, sort_keys=False))

    def copy_prediction(item, *, digests_root, **kwargs):
        destination = (
            digests_root
            / "variants"
            / item["friendly_name"]
            / "reference.fixture-run.yaml"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(sources[item["id"]].read_bytes())
        return destination

    result = run_experiment(
        FIXTURE,
        STUB_BASELINE,
        tmp_path / "output",
        model="sonnet",
        use_api=False,
        dispatch_gate=_gate,
        stub_responses=RESPONSES,
        adjudication_path=sidecar_path,
        run_id="adjudicated-run",
        extractor=copy_prediction,
        repo_root=REPO,
    )

    recorded = result["report"]["semantic_adjudication"]
    assert recorded["judge"] == {
        "kind": "human",
        "identifier": "fixture-reviewer",
    }
    assert recorded["metadata"] == {"experiment": "accepted-paraphrases"}
    assert recorded["sha256"] == file_sha256(sidecar_path)


def test_metered_cli_refuses_before_extraction_without_confirmation(
    tmp_path, monkeypatch
):
    from digester import cli, fixture_experiment

    gate_seen = []

    def exercise_gate(*args, dispatch_gate, **kwargs):
        prepared = [
            {
                "id": "a",
                "path": tmp_path / "a.md",
                "parsed": SimpleNamespace(body="abc"),
            },
            {
                "id": "b",
                "path": tmp_path / "b.md",
                "parsed": SimpleNamespace(body="12345"),
            },
        ]
        gate_seen.append(True)
        dispatch_gate(prepared)
        raise AssertionError("an unapproved run reached extraction")

    estimates = []
    monkeypatch.setattr(fixture_experiment, "run_experiment", exercise_gate)
    monkeypatch.setattr(cli, "resolve_use_api", lambda _: True)
    monkeypatch.setattr(cli, "is_metered", lambda model, use_api: True)
    monkeypatch.setattr(cli, "_authorise_hosted_input", lambda *args: object())
    monkeypatch.setattr(
        cli,
        "estimate_batch",
        lambda sizes, model: estimates.append(sizes) or {"records": 2},
    )
    monkeypatch.setattr(cli, "spend_confirmed", lambda *args, **kwargs: False)

    result = CliRunner().invoke(
        main,
        [
            "fixture-experiment",
            "--fixture",
            str(FIXTURE),
            "--baseline",
            str(QUALITY_BASELINE),
            "--output-root",
            str(tmp_path / "out"),
            "--model",
            "metered-test-model",
        ],
    )

    assert result.exit_code != 0
    assert gate_seen == [True]
    assert estimates == [[3, 5]]
    assert "not approved" in result.output


def test_committed_stub_baseline_is_bound_to_fixture_and_expected_stub_score():
    baseline = json.loads(STUB_BASELINE.read_text())
    assert baseline["fixture_sha256"] == file_sha256(FIXTURE)
    assert baseline["source"]["stub_responses_sha256"] == file_sha256(RESPONSES)
    assert baseline["score"]["dimensions"] == {
        "coverage": {"passed": 6, "total": 6, "rate": 1.0},
        "context": {"passed": 0, "total": 1, "rate": 0.0},
        "dates": {"passed": 2, "total": 3, "rate": 2 / 3},
        "references": {"passed": 4, "total": 4, "rate": 1.0},
        "accounts": {"passed": 0, "total": 1, "rate": 0.0},
        "chronology": {"passed": 0, "total": 3, "rate": 0.0},
    }


def test_committed_quality_baseline_records_the_genuine_sonnet_run():
    baseline = load_baseline(
        QUALITY_BASELINE, file_sha256(FIXTURE), stubbed_model=False
    )
    assert baseline["fixture_sha256"] == file_sha256(FIXTURE)
    assert baseline["source"]["kind"] == "genuine-experiment-report"
    assert baseline["source"]["run_id"] == "20260917T072611Z-sonnet-afeb3cc0"
    assert baseline["source"]["model"] == "sonnet"
    assert baseline["source"]["effective_configuration"]["model"]["route"] == "cli"
    assert baseline["source"]["code_revision"] == {
        "commit": "6f84a1317c16ad3dd06c965702ad56959c6ee7a7",
        "dirty": True,
    }
    assert baseline["score"]["dimensions"]["coverage"] == {
        "passed": 5,
        "total": 6,
        "rate": 5 / 6,
    }


def test_stub_response_file_is_plain_data_not_a_provider_configuration():
    document = yaml.safe_load(RESPONSES.read_text())
    assert document["schema"] == "anomalica/digestion-fixture-stub-responses/1"
    assert "provider" not in document


@pytest.mark.parametrize(
    ("extra_args", "expected"),
    [
        (["--stub-responses", str(RESPONSES)], STUB_BASELINE),
        ([], QUALITY_BASELINE),
    ],
)
def test_cli_selects_the_baseline_for_the_run_kind(
    tmp_path, monkeypatch, extra_args, expected
):
    from digester import cli, fixture_experiment

    seen = []
    run_dir = tmp_path / "result"
    run_dir.mkdir()
    (run_dir / "summary.txt").write_text("unchanged: test\n")

    def capture(fixture, baseline, output_root, **kwargs):
        seen.append(baseline)
        return {
            "run_dir": run_dir,
            "report": {"baseline_comparison": {"outcome": "unchanged"}},
        }

    monkeypatch.setattr(fixture_experiment, "run_experiment", capture)
    monkeypatch.setattr(cli, "resolve_use_api", lambda _: False)
    result = CliRunner().invoke(main, ["fixture-experiment", *extra_args])

    assert result.exit_code == 0
    assert seen == [expected]


def test_stub_and_quality_baselines_cannot_be_interchanged(tmp_path):
    with pytest.raises(ExperimentError, match="stub baseline"):
        run_experiment(
            FIXTURE,
            QUALITY_BASELINE,
            tmp_path / "stub",
            model="sonnet",
            use_api=False,
            dispatch_gate=_gate,
            stub_responses=RESPONSES,
            repo_root=REPO,
        )
    with pytest.raises(ExperimentError, match="quality baseline"):
        run_experiment(
            FIXTURE,
            STUB_BASELINE,
            tmp_path / "quality",
            model="sonnet",
            use_api=False,
            dispatch_gate=lambda _: pytest.fail("baseline failed after dispatch gate"),
            repo_root=REPO,
        )


def _acceptance_report(tmp_path: Path) -> Path:
    result = run_experiment(
        FIXTURE,
        STUB_BASELINE,
        tmp_path / "source",
        model="sonnet",
        use_api=False,
        dispatch_gate=_gate,
        stub_responses=RESPONSES,
        run_id="acceptance-source",
        repo_root=REPO,
    )
    return result["run_dir"] / "report.json"


def _mark_report_nonstub(report: Path) -> dict:
    document = json.loads(report.read_text())
    document["stubbed_model"] = False
    document["stub_responses"] = None
    document["stub_calls"] = None
    report.write_text(json.dumps(document))
    return document


def test_stub_run_cannot_become_quality_baseline(tmp_path):
    report = _acceptance_report(tmp_path)

    with pytest.raises(ExperimentError, match="stubbed experiment"):
        accept_quality_baseline(report, FIXTURE, tmp_path / "quality.json")


def test_failed_run_cannot_become_quality_baseline(tmp_path):
    report = _acceptance_report(tmp_path)
    document = _mark_report_nonstub(report)
    document["status"] = "failed"
    report.write_text(json.dumps(document))

    with pytest.raises(ExperimentError, match="complete experiment"):
        accept_quality_baseline(report, FIXTURE, tmp_path / "quality.json")


def test_stale_fixture_cannot_become_quality_baseline(tmp_path):
    report = _acceptance_report(tmp_path)
    _mark_report_nonstub(report)
    stale_fixture = tmp_path / "cases.yaml"
    stale_fixture.write_bytes(FIXTURE.read_bytes() + b"\n")

    with pytest.raises(ExperimentError, match="stale for the current fixture"):
        accept_quality_baseline(report, stale_fixture, tmp_path / "quality.json")


def test_changed_prediction_cannot_become_quality_baseline(tmp_path):
    report = _acceptance_report(tmp_path)
    document = _mark_report_nonstub(report)
    prediction = next(iter(document["predictions"].values()))
    artifact = report.parent / prediction["path"]
    artifact.write_bytes(artifact.read_bytes() + b"\n")

    with pytest.raises(ExperimentError, match="hash is stale"):
        accept_quality_baseline(report, FIXTURE, tmp_path / "quality.json")


def test_valid_real_report_can_be_accepted_regardless_of_old_comparison(tmp_path):
    report = _acceptance_report(tmp_path)
    document = _mark_report_nonstub(report)
    document["baseline_comparison"]["outcome"] = "worse"
    report.write_text(json.dumps(document))
    output = tmp_path / "quality.json"

    baseline = accept_quality_baseline(report, FIXTURE, output)

    assert baseline["schema"] == "anomalica/digestion-fixture-quality-baseline/1"
    assert baseline["source"]["run_id"] == "acceptance-source"
    assert baseline["source"]["report_sha256"] == file_sha256(report)
    assert baseline["source"]["extraction_config"].startswith("sha256:")
    assert baseline["source"]["prediction_sha256"]
    assert baseline["score"] == document["score"]
    assert json.loads(output.read_text()) == baseline
