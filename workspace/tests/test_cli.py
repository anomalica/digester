import json
from pathlib import Path

from click.testing import CliRunner

from digester.cli import _producer_from_creators, main


def test_producer_is_first_real_creator():
    assert _producer_from_creators(["60 Minutes"]) == "60 Minutes"
    assert _producer_from_creators(["Helene Cooper", "Leslie Kean"]) == "Helene Cooper"


def test_producer_skips_annotation_tokens():
    # The 1970 Los Alamos case: a redaction token must not become the producer.
    assert _producer_from_creators(["{{redacted}}", "James L. Tuck"]) == "James L. Tuck"
    assert _producer_from_creators(["{{ redacted }}", "Real Name"]) == "Real Name"


def test_producer_none_when_no_real_creator():
    assert _producer_from_creators([]) is None
    assert _producer_from_creators(None) is None
    assert _producer_from_creators(["{{redacted}}"]) is None


def _ingests_tree(tmp_path: Path, *, digestible: bool):
    h = "c" * 64
    (tmp_path / "store" / "v1").mkdir(parents=True)
    (tmp_path / "by-name").mkdir()
    body = "---\nschema: anomalica/record/1\ntitle: T\n---\n00:00:01.0 A sentence.\n"
    (tmp_path / "store" / "v1" / f"{h}.md").write_text(body)
    sidecar = {
        "schema": "anomalica/review-coverage/1",
        "observed_coverage": 1.0 if digestible else 0.4,
        "digestible": digestible,
        "total_units": 1,
        "reviews": [],
    }
    (tmp_path / "store" / f"{h}.review.json").write_text(json.dumps(sidecar))
    (tmp_path / "by-name" / "rec.md").symlink_to(
        Path("..") / "store" / "v1" / f"{h}.md"
    )
    return tmp_path / "by-name"


def test_coverage_command_reports_digestible(tmp_path):
    records = _ingests_tree(tmp_path, digestible=True)
    result = CliRunner().invoke(main, ["coverage", str(records)])
    assert result.exit_code == 0
    assert "1/1 records digestible" in result.output
    assert "YES" in result.output


def test_coverage_command_reports_not_digestible(tmp_path):
    records = _ingests_tree(tmp_path, digestible=False)
    result = CliRunner().invoke(main, ["coverage", str(records)])
    assert result.exit_code == 0
    assert "0/1 records digestible" in result.output
    assert "40.0%" in result.output
