"""Agent-operated extraction experiments over the synthetic behaviour fixtures."""

from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from digester import fixture_eval
from digester.record_parser import parse_record


REPORT_SCHEMA = "anomalica/digestion-fixture-experiment/1"
STUB_BASELINE_SCHEMA = "anomalica/digestion-fixture-stub-baseline/1"
QUALITY_BASELINE_SCHEMA = "anomalica/digestion-fixture-quality-baseline/1"
STUB_SCHEMA = "anomalica/digestion-fixture-stub-responses/1"
DIMENSION_LABELS = {
    "coverage": "expected ideas",
    "context": "pronouns and grouped context",
    "dates": "dates and date ranges",
    "references": "required people, topics and events",
    "accounts": "claims grouped into each source telling",
    "chronology": "events ordered within each telling",
}


class ExperimentError(ValueError):
    """The experiment could not produce a trustworthy comparison."""


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def _write_json(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def code_revision(repo: Path) -> dict:
    """Record the Git revision and whether any tracked or untracked work exists."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExperimentError("cannot identify the experiment code revision") from exc
    return {"commit": revision, "dirty": dirty}


def _rate(summary: dict) -> float | None:
    value = summary.get("rate")
    return float(value) if isinstance(value, (int, float)) else None


def compare_scores(current: dict, baseline: dict) -> dict:
    """Compare independent dimensions without collapsing them into one score."""
    dimensions = {}
    better = worse = 0
    for name in fixture_eval.DIMENSIONS:
        now = current["dimensions"][name]
        before = baseline["dimensions"][name]
        now_rate, before_rate = _rate(now), _rate(before)
        if now_rate is None and before_rate is None:
            status, delta = "unchanged", None
        elif before_rate is None:
            status, delta = "better", None
            better += 1
        elif now_rate is None:
            status, delta = "worse", None
            worse += 1
        else:
            delta = now_rate - before_rate
            if delta > 0:
                status = "better"
                better += 1
            elif delta < 0:
                status = "worse"
                worse += 1
            else:
                status = "unchanged"
        dimensions[name] = {
            "label": DIMENSION_LABELS[name],
            "status": status,
            "baseline": before,
            "current": now,
            "rate_change": delta,
        }
    outcome = (
        "mixed"
        if better and worse
        else "better"
        if better
        else "worse"
        if worse
        else "unchanged"
    )
    return {"outcome": outcome, "dimensions": dimensions}


def failed_behaviours(score: dict) -> list[dict]:
    """List failed expectations in ordinary language for the report lead."""
    failures = []
    for case in score["cases"]:
        for item in case["expectations"]:
            if not item["passed"]:
                failures.append(
                    {
                        "case": case["case"].replace("-", " "),
                        "dimension": DIMENSION_LABELS[item["dimension"]],
                        "behaviour": item["expectation"].replace("-", " "),
                        "evidence": item["evidence"],
                    }
                )
    return failures


def format_summary(comparison: dict, failures: list[dict]) -> str:
    """Human report led by better/worse/unchanged/mixed."""
    outcome = comparison["outcome"]
    changed = [
        row for row in comparison["dimensions"].values() if row["status"] != "unchanged"
    ]
    if not changed:
        lead = f"{outcome}: every scored dimension matches the accepted baseline."
    else:
        changes = ", ".join(f"{row['label']} {row['status']}" for row in changed)
        lead = f"{outcome}: {changes}."
    lines = [lead, "", "Per dimension:"]
    for row in comparison["dimensions"].values():
        current = row["current"]
        baseline = row["baseline"]
        lines.append(
            f"- {row['label']}: {row['status']} "
            f"({current['passed']}/{current['total']} now; "
            f"{baseline['passed']}/{baseline['total']} baseline)"
        )
    lines.extend(["", "Failed behaviours:"])
    if failures:
        lines.extend(
            f"- {item['case']}: {item['dimension']} - {item['behaviour']} ({item['evidence']})"
            for item in failures
        )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _record_text(case: dict) -> tuple[str, str]:
    import hashlib

    body = case["source"].strip() + "\n"
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    frontmatter = {
        "schema": "anomalica/record/1",
        "title": f"Synthetic digestion fixture: {case['id']}",
        "creators": ["Anomalica synthetic fixture"],
        "source_type": "text",
        "content_hash": f"sha256:{content_hash}",
        "copyright": {"status": "public_domain"},
    }
    text = f"---\n{yaml.safe_dump(frontmatter, sort_keys=False).strip()}\n---\n{body}"
    return content_hash, text


def prepare_records(fixture: dict, run_dir: Path) -> list[dict]:
    """Materialise valid ephemeral records in a content-addressed local store."""
    store = run_dir / "records" / "store"
    store.mkdir(parents=True, exist_ok=True)
    prepared = []
    for case in fixture["cases"]:
        content_hash, text = _record_text(case)
        path = store / f"{content_hash}.md"
        path.write_text(text)
        parsed = parse_record(text)
        prepared.append(
            {
                "id": case["id"],
                "friendly_name": f"fixture-{_safe(case['id'])}",
                "path": path,
                "parsed": parsed,
            }
        )
    return prepared


class StubModel:
    """Serve canned provider responses at the production transport seam."""

    def __init__(self, document: dict, case_ids: list[str]):
        if document.get("schema") != STUB_SCHEMA:
            raise ExperimentError(f"stub response schema must be {STUB_SCHEMA}")
        responses = document.get("cases")
        if not isinstance(responses, dict) or set(responses) != set(case_ids):
            raise ExperimentError("stub responses must cover the exact fixture cases")
        for case_id, passes in responses.items():
            if not isinstance(passes, dict) or set(passes) != {"nodes", "claims"}:
                raise ExperimentError(f"stub response {case_id} needs nodes and claims")
        self.responses = responses
        self.case_id: str | None = None
        self.calls: list[dict] = []
        self._original = None

    def __enter__(self):
        from digester import extract

        self._original = extract.call_with_document
        extract.call_with_document = self.call
        return self

    def __exit__(self, *_):
        from digester import extract

        extract.call_with_document = self._original

    def call(self, preamble, document, task, model, schema=None, use_api=False, **_):
        if self.case_id is None:
            raise ExperimentError("stub model called without an active fixture case")
        properties = (schema or {}).get("properties") or {}
        pass_name = (
            "nodes"
            if "nodes" in properties
            else "claims"
            if "claims" in properties
            else None
        )
        if pass_name is None:
            raise ExperimentError("stub model received an unknown production schema")
        self.calls.append(
            {
                "case": self.case_id,
                "pass": pass_name,
                "model": model,
                "use_api": use_api,
            }
        )
        return json.dumps(self.responses[self.case_id][pass_name])


def _validate_score_shape(score: object, fixture: dict | None = None) -> dict:
    if not isinstance(score, dict):
        raise ExperimentError("fixture score must be a mapping")
    dimensions = score.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(
        fixture_eval.DIMENSIONS
    ):
        raise ExperimentError("fixture score has incomplete dimensions")
    for name, summary in dimensions.items():
        if not isinstance(summary, dict) or set(summary) != {"passed", "total", "rate"}:
            raise ExperimentError(f"fixture score dimension {name} is malformed")
        passed, total, rate = summary["passed"], summary["total"], summary["rate"]
        if (
            isinstance(passed, bool)
            or not isinstance(passed, int)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or passed < 0
            or total < 0
            or passed > total
        ):
            raise ExperimentError(f"fixture score dimension {name} has invalid counts")
        expected_rate = passed / total if total else None
        if rate != expected_rate:
            raise ExperimentError(f"fixture score dimension {name} has an invalid rate")
    if fixture is not None:
        if score.get("schema") != fixture_eval.SCHEMA:
            raise ExperimentError("experiment score schema is unsupported")
        cases = score.get("cases")
        expected_cases = {case["id"] for case in fixture["cases"]}
        if (
            not isinstance(cases, list)
            or {case.get("case") for case in cases if isinstance(case, dict)}
            != expected_cases
        ):
            raise ExperimentError(
                "experiment score does not cover the exact fixture cases"
            )
        if len(cases) != len(expected_cases):
            raise ExperimentError("experiment score has duplicate fixture cases")
        for case in cases:
            _validate_score_shape({"dimensions": case.get("dimensions")})
            if not isinstance(case.get("expectations"), list):
                raise ExperimentError("experiment case lacks expectation evidence")
    return score


def load_baseline(path: Path, fixture_hash: str, *, stubbed_model: bool) -> dict:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read baseline {path}") from exc
    expected_schema = STUB_BASELINE_SCHEMA if stubbed_model else QUALITY_BASELINE_SCHEMA
    if document.get("schema") != expected_schema:
        kind = "stub" if stubbed_model else "quality"
        raise ExperimentError(f"fixture experiment requires the {kind} baseline")
    if document.get("fixture_sha256") != fixture_hash:
        raise ExperimentError("fixture baseline is stale for the current fixture bytes")
    source = document.get("source")
    expected_kind = (
        "deterministic-stub-experiment"
        if stubbed_model
        else "genuine-experiment-report"
    )
    if not isinstance(source, dict) or source.get("kind") != expected_kind:
        raise ExperimentError("fixture baseline source metadata is incompatible")
    if stubbed_model:
        if not isinstance(source.get("stub_responses_sha256"), str):
            raise ExperimentError("stub baseline lacks its response hash")
    else:
        required_strings = (
            "report_sha256",
            "run_id",
            "completed_at",
            "model",
            "extraction_config",
        )
        if not all(
            isinstance(source.get(name), str) and source[name]
            for name in required_strings
        ):
            raise ExperimentError("quality baseline lacks accepted report provenance")
        if (
            not isinstance(source.get("use_api"), bool)
            or not isinstance(source.get("effective_configuration"), dict)
            or not isinstance(source.get("code_revision"), dict)
            or not isinstance(source.get("prediction_sha256"), dict)
        ):
            raise ExperimentError("quality baseline provenance is incomplete")
    _validate_score_shape(document.get("score"))
    return document


def _resolve_report_artifact(report_path: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
        raise ExperimentError(
            "experiment prediction path must be relative to its report"
        )
    resolved = (report_path.parent / raw_path).resolve()
    if not resolved.is_relative_to(report_path.parent.resolve()):
        raise ExperimentError("experiment prediction path escapes its run directory")
    variants_root = (report_path.parent / "digests" / "variants").resolve()
    if not resolved.is_relative_to(variants_root):
        raise ExperimentError("quality baseline predictions must be variant artifacts")
    return resolved


def accept_quality_baseline(
    report_path: Path, fixture_path: Path, output_path: Path
) -> dict:
    """Validate a genuine completed report and persist its self-contained baseline."""
    report, report_hash = fixture_eval.load_hashed_document(report_path)
    fixture, fixture_hash = fixture_eval.load_hashed_document(fixture_path)
    fixture_eval.validate_fixture(fixture)
    if report.get("schema") != REPORT_SCHEMA or report.get("status") != "complete":
        raise ExperimentError("quality baseline requires a complete experiment report")
    if report.get("stubbed_model") is not False:
        raise ExperimentError("a stubbed experiment cannot become the quality baseline")
    if report.get("stub_responses") is not None or report.get("stub_calls") is not None:
        raise ExperimentError("quality baseline report contains stub metadata")
    if (report.get("fixture") or {}).get("sha256") != fixture_hash:
        raise ExperimentError(
            "experiment report is stale for the current fixture bytes"
        )

    model = report.get("model")
    run_id = report.get("run_id")
    completed_at = report.get("completed_at")
    extraction_config = report.get("extraction_config")
    configuration = report.get("effective_configuration")
    revision = report.get("code_revision")
    if not all(
        isinstance(value, str) and value for value in (model, run_id, completed_at)
    ):
        raise ExperimentError("experiment report lacks model or run identity")
    if not isinstance(report.get("use_api"), bool):
        raise ExperimentError("experiment report lacks route metadata")
    if not isinstance(extraction_config, str) or not extraction_config.startswith(
        "sha256:"
    ):
        raise ExperimentError("experiment report lacks an extraction fingerprint")
    if not isinstance(configuration, dict) or not isinstance(
        configuration.get("model"), dict
    ):
        raise ExperimentError("experiment report lacks effective configuration")
    configuration_model = configuration["model"]
    if configuration_model.get("requested") != model or not isinstance(
        configuration_model.get("route"), str
    ):
        raise ExperimentError(
            "experiment report model conflicts with its configuration"
        )
    from digester.extraction_config_registry import fingerprint

    if fingerprint(configuration) != extraction_config:
        raise ExperimentError("experiment extraction fingerprint is stale")
    if (
        not isinstance(revision, dict)
        or not isinstance(revision.get("commit"), str)
        or not isinstance(revision.get("dirty"), bool)
    ):
        raise ExperimentError("experiment report lacks code revision metadata")

    score = _validate_score_shape(report.get("score"), fixture)
    prediction_rows = report.get("predictions")
    case_ids = {case["id"] for case in fixture["cases"]}
    if not isinstance(prediction_rows, dict) or set(prediction_rows) != case_ids:
        raise ExperimentError(
            "experiment predictions do not cover the exact fixture cases"
        )
    predictions = {}
    prediction_hashes = {}
    for case_id, row in prediction_rows.items():
        if not isinstance(row, dict):
            raise ExperimentError(f"experiment prediction {case_id} is malformed")
        path = _resolve_report_artifact(report_path, row.get("path"))
        prediction, prediction_hash = fixture_eval.load_hashed_document(path)
        if row.get("sha256") != prediction_hash:
            raise ExperimentError(f"experiment prediction {case_id} hash is stale")
        predictions[case_id] = prediction
        prediction_hashes[case_id] = prediction_hash

    adjudications = None
    adjudication_source = report.get("semantic_adjudication")
    if adjudication_source is not None:
        if not isinstance(adjudication_source, dict):
            raise ExperimentError("experiment semantic adjudication is malformed")
        raw_path = adjudication_source.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ExperimentError(
                "experiment semantic adjudication lacks its source path"
            )
        adjudication_path = Path(raw_path)
        if not adjudication_path.is_absolute():
            adjudication_path = report_path.parent / adjudication_path
        if fixture_eval.file_sha256(adjudication_path) != adjudication_source.get(
            "sha256"
        ):
            raise ExperimentError("experiment semantic adjudication hash is stale")
        adjudications = fixture_eval.validate_adjudications(
            fixture_eval.load_document(adjudication_path),
            fixture_sha256=fixture_hash,
            prediction_sha256=prediction_hashes,
            fixture=fixture,
            predictions=predictions,
        )
    if fixture_eval.score(fixture, predictions, adjudications=adjudications) != score:
        raise ExperimentError(
            "experiment score does not match its prediction artifacts"
        )

    baseline = {
        "schema": QUALITY_BASELINE_SCHEMA,
        "fixture_sha256": fixture_hash,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "kind": "genuine-experiment-report",
            "report_sha256": report_hash,
            "run_id": run_id,
            "completed_at": completed_at,
            "model": model,
            "use_api": report["use_api"],
            "extraction_config": extraction_config,
            "effective_configuration": configuration,
            "code_revision": revision,
            "prediction_sha256": prediction_hashes,
            "semantic_adjudication": (
                {
                    "sha256": adjudication_source["sha256"],
                    "judge": adjudication_source.get("judge"),
                    "metadata": adjudication_source.get("metadata"),
                }
                if adjudication_source is not None
                else None
            ),
        },
        "score": score,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    _write_json(temporary, baseline)
    temporary.replace(output_path)
    return baseline


def _default_extractor(
    item: dict,
    *,
    model: str,
    use_api: bool,
    authority,
    digests_root: Path,
    run_label: str,
) -> Path:
    from digester.cli import _do_extract

    return _do_extract(
        item["path"],
        item["parsed"],
        Path(f"{item['friendly_name']}.yaml"),
        model,
        use_api,
        digests_root,
        True,
        None,
        run_label,
        input_authority=authority,
    )


def run_experiment(
    fixture_path: Path,
    baseline_path: Path,
    output_root: Path,
    *,
    model: str,
    use_api: bool,
    dispatch_gate: Callable[[list[dict]], dict[str, object]],
    stub_responses: Path | None = None,
    adjudication_path: Path | None = None,
    run_id: str | None = None,
    extractor: Callable[..., Path] = _default_extractor,
    repo_root: Path | None = None,
) -> dict:
    """Run an experiment, forbidding every model in deterministic stub mode."""
    previous_entailment = os.environ.get("DIGESTER_ENTAILMENT")
    if stub_responses is not None:
        os.environ["DIGESTER_ENTAILMENT"] = "off"
    try:
        return _run_experiment(
            fixture_path,
            baseline_path,
            output_root,
            model=model,
            use_api=use_api,
            dispatch_gate=dispatch_gate,
            stub_responses=stub_responses,
            adjudication_path=adjudication_path,
            run_id=run_id,
            extractor=extractor,
            repo_root=repo_root,
        )
    finally:
        if stub_responses is not None:
            if previous_entailment is None:
                os.environ.pop("DIGESTER_ENTAILMENT", None)
            else:
                os.environ["DIGESTER_ENTAILMENT"] = previous_entailment


def _run_experiment(
    fixture_path: Path,
    baseline_path: Path,
    output_root: Path,
    *,
    model: str,
    use_api: bool,
    dispatch_gate: Callable[[list[dict]], dict[str, object]],
    stub_responses: Path | None = None,
    adjudication_path: Path | None = None,
    run_id: str | None = None,
    extractor: Callable[..., Path] = _default_extractor,
    repo_root: Path | None = None,
) -> dict:
    """Extract, score, compare and persist one reproducible fixture experiment."""
    fixture, fixture_hash = fixture_eval.load_hashed_document(fixture_path)
    fixture_eval.validate_fixture(fixture)
    baseline = load_baseline(
        baseline_path, fixture_hash, stubbed_model=stub_responses is not None
    )
    stub_hash = fixture_eval.file_sha256(stub_responses) if stub_responses else None
    if stub_hash is not None:
        baseline_source = baseline.get("source")
        if (
            not isinstance(baseline_source, dict)
            or baseline_source.get("stub_responses_sha256") != stub_hash
        ):
            raise ExperimentError(
                "fixture baseline is stale for the stub response bytes"
            )
    now = datetime.now(timezone.utc)
    run_id = (
        run_id
        or f"{now.strftime('%Y%m%dT%H%M%SZ')}-{_safe(model)}-{fixture_hash[7:15]}"
    )
    run_dir = output_root / "runs" / run_id
    if run_dir.exists():
        raise ExperimentError(f"experiment run already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    prepared = prepare_records(fixture, run_dir)
    repo_root = repo_root or Path(__file__).resolve().parents[2]

    from anomalica_common.pre_digest import PREP_VERSION
    from digester.extract import effective_extraction_configuration
    from digester.extraction_config_registry import fingerprint

    configuration = effective_extraction_configuration(
        model, prep_version=PREP_VERSION, use_api=use_api
    )
    metadata = {
        "schema": REPORT_SCHEMA,
        "status": "running",
        "run_id": run_id,
        "started_at": now.isoformat(),
        "model": model,
        "use_api": use_api,
        "stubbed_model": stub_responses is not None,
        "stub_responses": (
            {"path": str(stub_responses), "sha256": stub_hash}
            if stub_responses is not None
            else None
        ),
        "fixture": {"path": str(fixture_path), "sha256": fixture_hash},
        "baseline": {"path": str(baseline_path)},
        "extraction_config": fingerprint(configuration),
        "effective_configuration": configuration,
        "code_revision": code_revision(repo_root),
        "predictions": {},
        "semantic_adjudication": None,
    }
    _write_json(run_dir / "run.json", metadata)

    stub = None
    if stub_responses is not None:
        stub = StubModel(
            fixture_eval.load_document(stub_responses),
            [item["id"] for item in prepared],
        )
    context = stub if stub is not None else nullcontext()
    current_case = None
    try:
        authorities = dispatch_gate(prepared)
        if set(authorities) != {item["id"] for item in prepared}:
            raise ExperimentError("dispatch gate did not authorise every fixture case")
        digests_root = run_dir / "digests"
        predictions = {}
        prediction_hashes = {}
        with context:
            for item in prepared:
                current_case = item["id"]
                if stub is not None:
                    stub.case_id = current_case
                path = extractor(
                    item,
                    model=model,
                    use_api=use_api,
                    authority=authorities[current_case],
                    digests_root=digests_root,
                    run_label=run_id,
                )
                prediction, digest_hash = fixture_eval.load_hashed_document(path)
                predictions[current_case] = prediction
                prediction_hashes[current_case] = digest_hash
                metadata["predictions"][current_case] = {
                    "path": str(path.relative_to(run_dir)),
                    "sha256": digest_hash,
                }

        adjudications = None
        if adjudication_path is not None:
            adjudications = fixture_eval.validate_adjudications(
                fixture_eval.load_document(adjudication_path),
                fixture_sha256=fixture_hash,
                prediction_sha256=prediction_hashes,
                fixture=fixture,
                predictions=predictions,
            )
            metadata["semantic_adjudication"] = {
                "path": str(adjudication_path),
                "sha256": fixture_eval.file_sha256(adjudication_path),
                "judge": {
                    "kind": adjudications.judge_kind,
                    "identifier": adjudications.judge_identifier,
                },
                "metadata": adjudications.metadata,
            }

        score = fixture_eval.score(fixture, predictions, adjudications=adjudications)
        comparison = compare_scores(score, baseline["score"])
        failures = failed_behaviours(score)
        metadata.update(
            {
                "status": "complete",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "score": score,
                "baseline_comparison": comparison,
                "failed_behaviours": failures,
                "stub_calls": stub.calls if stub is not None else None,
            }
        )
        _write_json(run_dir / "report.json", metadata)
        _write_json(run_dir / "run.json", metadata)
        (run_dir / "summary.txt").write_text(format_summary(comparison, failures))
        return {"run_dir": run_dir, "report": metadata}
    except Exception as exc:
        metadata.update(
            {
                "status": "failed",
                "failed_case": current_case,
                "error": f"{type(exc).__name__}: {exc}",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write_json(run_dir / "failure.json", metadata)
        _write_json(run_dir / "run.json", metadata)
        raise
