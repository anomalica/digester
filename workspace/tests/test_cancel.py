import pytest
from click.testing import CliRunner

from anomalica_common.llm.allowance import Allowance

from digester import cli, extract
from digester.cli import main
from digester.extract import (
    ExtractionCancelled,
    _check_cancel,
    request_cancel,
    reset_cancel,
)


def test_cancel_flag_lifecycle():
    reset_cancel()
    _check_cancel()  # no-op when not requested
    request_cancel()
    with pytest.raises(ExtractionCancelled):
        _check_cancel()
    reset_cancel()
    _check_cancel()  # cleared again


def test_nodes_pass_honours_cancel_before_any_model_call(monkeypatch):
    calls = []
    monkeypatch.setattr(extract, "_call", lambda *a, **k: calls.append(1) or "{}")
    request_cancel()
    try:
        with pytest.raises(ExtractionCancelled):
            extract.extract_nodes_v2("some text to extract from", model="haiku")
    finally:
        reset_cancel()
    assert calls == []  # stopped before dispatching any call


def test_two_pass_resets_stale_cancel(monkeypatch):
    # A cancel left set by a prior run in the same process must not abort the next.
    request_cancel()
    reset_after = {}

    def fake_nodes(*a, **k):
        reset_after["cancel_cleared"] = not extract._cancel_requested
        raise RuntimeError("stop here - only checking the reset ran first")

    monkeypatch.setattr(extract, "extract_nodes_v2", fake_nodes)
    with pytest.raises(RuntimeError):
        extract.extract_two_pass("text", model="haiku")
    reset_cancel()
    assert reset_after["cancel_cleared"] is True


@pytest.fixture(autouse=True)
def _allowance_open(monkeypatch):
    """Isolate the exit-code contract from the live plan meter.

    These tests assert what the command exits WITH; without this they also
    depend on Mark's actual allowance at the moment they run. The meter is
    intermittent by design - allowance.py documents roughly one call in three
    returning nothing - and check_allowance correctly FAILS CLOSED, so a blink
    makes the command exit 77 (allowance ceiling) and the cancel test reports a
    75-vs-77 mismatch that has nothing to do with cancelling.

    That is a test coupled to state it does not control and does not mean to
    exercise. The ceiling has its own tests; this one is about exit codes.
    """
    monkeypatch.setattr(cli, "check_allowance", lambda **k: Allowance(True, "test"))


def test_extract_command_exits_75_on_cancel(tmp_path, monkeypatch):
    # The scheduler's contract: a clean cancel exits 75, distinct from a real
    # failure (1). No model call - _do_extract is stubbed to raise the cancel.
    rec = tmp_path / "rec.md"
    rec.write_text("---\ntitle: Test\n---\nSome body text for the record.\n")

    def cancel(*a, **k):
        raise ExtractionCancelled()

    monkeypatch.setattr(cli, "_do_extract", cancel)
    result = CliRunner().invoke(main, ["extract", str(rec)])
    assert result.exit_code == 75
    assert "Completed chunks are cached" in result.output


def test_extract_command_exits_77_on_rate_limit(tmp_path, monkeypatch):
    # The scheduler's dispatcher only sees an exit code. On a FLAT plan throttling
    # is the governor rather than spend, so it must park+retry, never strike toward
    # skip the way a real extraction failure (exit 1) does.
    from anomalica_common.llm import OpencodeRateLimited

    rec = tmp_path / "rec.md"
    rec.write_text("---\ntitle: Test\n---\nSome body text for the record.\n")

    def throttled(*a, **k):
        raise OpencodeRateLimited("opencode throttled (model=opencode-go/glm-5.2)")

    monkeypatch.setattr(cli, "_do_extract", throttled)
    result = CliRunner().invoke(
        main, ["extract", str(rec), "--model", "opencode-go/glm-5.2"]
    )
    assert result.exit_code == 77
    assert "NOT an extraction failure" in result.output
