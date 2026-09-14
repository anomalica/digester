from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from digester import cli, extract
from digester.cli import main
from digester.input_rights import HostedInputRightsError, authorise_ordinary_extraction
from digester.record_parser import parse_record


MODEL = "opencode-go/test-model"


def _record(
    tmp_path: Path,
    status: object = "public_domain",
    *,
    declared_hash: str | None = None,
) -> Path:
    bare_hash = "a" * 64
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    copyright = "" if status is None else f"copyright:\n  status: {status}\n"
    text = (
        "---\n"
        "schema: anomalica/record/1\n"
        "title: Rights fixture\n"
        f"content_hash: {declared_hash or 'sha256:' + bare_hash}\n"
        f"{copyright}"
        "---\n"
        "Source body.\n"
    )
    path = store / f"{bare_hash}.md"
    path.write_text(text)
    return path


@pytest.mark.parametrize("status", ["public_domain", "open_licence"])
@pytest.mark.parametrize("variant", [False, True], ids=["direct", "variant-only"])
def test_open_statuses_reach_cli_extraction(tmp_path, monkeypatch, status, variant):
    record = _record(tmp_path, status)
    calls = []
    monkeypatch.setattr(cli, "_do_extract", lambda *a, **k: calls.append(k))
    args = ["extract", str(record), "--model", MODEL]
    if variant:
        args.extend(["--variant-only", "--digests-root", str(tmp_path / "digests")])

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0] == {}


@pytest.mark.parametrize("variant", [False, True], ids=["direct", "variant-only"])
@pytest.mark.parametrize(
    "status",
    [
        "publicly_accessible",
        "licensed",
        "restricted",
        None,
        "future_status",
        "[public_domain]",
    ],
    ids=[
        "publicly-accessible",
        "licensed",
        "restricted",
        "absent",
        "unrecognised",
        "malformed",
    ],
)
def test_ineligible_status_never_calls_provider_extraction(
    tmp_path, monkeypatch, status, variant
):
    record = _record(tmp_path, status)
    calls = []
    monkeypatch.setattr(extract, "extract_two_pass", lambda *a, **k: calls.append(True))
    args = ["extract", str(record), "--model", MODEL]
    if variant:
        args.extend(["--variant-only", "--digests-root", str(tmp_path / "digests")])

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 1
    assert "hosted input refused" in result.output
    assert calls == []


@pytest.mark.parametrize("variant", [False, True], ids=["direct", "variant-only"])
def test_content_hash_mismatch_never_calls_provider_extraction(
    tmp_path, monkeypatch, variant
):
    record = _record(tmp_path, declared_hash="sha256:" + "b" * 64)
    calls = []
    monkeypatch.setattr(extract, "extract_two_pass", lambda *a, **k: calls.append(True))
    args = ["extract", str(record), "--model", MODEL]
    if variant:
        args.extend(["--variant-only", "--digests-root", str(tmp_path / "digests")])

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 1
    assert "content_hash" in result.output
    assert calls == []


def test_provider_boundary_rejects_an_unbound_direct_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        extract,
        "_transport_call_with_document",
        lambda *a, **k: calls.append(True),
    )

    with pytest.raises(HostedInputRightsError, match="lacks exact"):
        extract.call_with_document("preamble", "source", "task", MODEL)

    assert calls == []


def test_provider_boundary_accepts_eligible_exact_record(tmp_path, monkeypatch):
    record = _record(tmp_path)
    authority = authorise_ordinary_extraction(record, MODEL)
    body = parse_record(record.read_text()).body
    calls = []
    monkeypatch.setattr(
        extract,
        "_transport_call_with_document",
        lambda *a, **k: calls.append((a, k)) or "{}",
    )

    with extract.provider_authority(authority, body):
        result = extract.call_with_document("preamble", body, "task", MODEL)

    assert result == "{}"
    assert len(calls) == 1


def test_provider_boundary_rechecks_live_record_bytes(tmp_path, monkeypatch):
    record = _record(tmp_path)
    authority = authorise_ordinary_extraction(record, MODEL)
    body = parse_record(record.read_text()).body
    calls = []
    monkeypatch.setattr(
        extract,
        "_transport_call_with_document",
        lambda *a, **k: calls.append(True),
    )
    record.write_text(record.read_text().replace("Source body.", "Changed body."))

    with extract.provider_authority(authority, body):
        with pytest.raises(HostedInputRightsError, match="lacks exact"):
            extract.call_with_document("preamble", body, "task", MODEL)

    assert calls == []


def test_malformed_frontmatter_never_calls_provider_extraction(tmp_path, monkeypatch):
    record = _record(tmp_path)
    record.write_text(record.read_text().replace("status: public_domain", "status: ["))
    calls = []
    monkeypatch.setattr(extract, "extract_two_pass", lambda *a, **k: calls.append(True))

    result = CliRunner().invoke(main, ["extract", str(record), "--model", MODEL])

    assert result.exit_code == 1
    assert "malformed frontmatter" in result.output
    assert calls == []


def test_accounts_denies_ineligible_input_before_extraction(tmp_path, monkeypatch):
    record = _record(tmp_path, "publicly_accessible")
    calls = []
    monkeypatch.setattr(extract, "extract_accounts", lambda *a, **k: calls.append(True))

    result = CliRunner().invoke(main, ["accounts", str(record), "--model", MODEL])

    assert result.exit_code == 1
    assert "hosted input refused" in result.output
    assert calls == []
