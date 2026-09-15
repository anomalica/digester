"""The Digester's exact-byte and Git-history review-gate wrappers."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from anomalica_common.review_gate import parsed_record_body
from digester.review_gate import assess_record, load_sidecar

RECORD_BODY = (
    "---\nschema: anomalica/record/1\ntitle: T\n---\n"
    "00:00:01.0 First sentence.\n"
    "00:00:03.0 Second sentence.\n"
)


def _make_store(tmp_path: Path, sidecar_at_root: bool) -> Path:
    """Build an ingests-like tree: records/<name>.md -> ../store/v1/<hash>.md,
    sidecar at the store root or colocated. Returns the records-dir record path."""
    h = "a" * 64
    (tmp_path / "store" / "v1").mkdir(parents=True)
    (tmp_path / "by-name").mkdir()
    (tmp_path / "store" / "v1" / f"{h}.md").write_text(RECORD_BODY)
    sidecar = {
        "schema": "anomalica/review-coverage/1",
        "observed_coverage": 1.0,
        "digestible": True,
        "total_units": 2,
        "reviews": [],
        "reviewed_body_sha256": "sha256:"
        + hashlib.sha256(parsed_record_body(RECORD_BODY).encode("utf-8")).hexdigest(),
    }

    sidecar_dir = tmp_path / "store" if sidecar_at_root else tmp_path / "store" / "v1"
    (sidecar_dir / f"{h}.review.json").write_text(json.dumps(sidecar))
    link = tmp_path / "by-name" / "rec.md"
    link.symlink_to(Path("..") / "store" / "v1" / f"{h}.md")
    return link


def test_load_sidecar_resolves_at_store_root(tmp_path):
    # .md under store/v1/, sidecar at store/ root - must still resolve.
    rec = _make_store(tmp_path, sidecar_at_root=True)
    assert load_sidecar(rec, tmp_path) is not None


def test_load_sidecar_resolves_colocated(tmp_path):
    rec = _make_store(tmp_path, sidecar_at_root=False)
    assert load_sidecar(rec, tmp_path) is not None


def test_assess_record_end_to_end(tmp_path):
    rec = _make_store(tmp_path, sidecar_at_root=True)
    d = assess_record(rec, tmp_path)
    assert d.digestible
    assert d.observed_coverage == 1.0
    assert d.source == "sidecar"


def test_load_sidecar_resolves_v2_body_to_bare_hash_sidecar(tmp_path):
    # A v2 body is {hash}.v2.md but its sidecar is {hash}.review.json (no .v2).
    # The .v2 infix must be stripped or every v2 record reads as unreviewed.
    h = "c" * 64
    (tmp_path / "store").mkdir(parents=True)
    body = tmp_path / "store" / f"{h}.v2.md"
    body.write_text(RECORD_BODY)
    (tmp_path / "store" / f"{h}.review.json").write_text(
        json.dumps(
            {
                "schema": "anomalica/review-coverage/1",
                "observed_coverage": 1.0,
                "digestible": True,
                "total_units": 2,
                "reviews": [],
                "reviewed_body_sha256": "sha256:"
                + hashlib.sha256(
                    parsed_record_body(RECORD_BODY).encode("utf-8")
                ).hexdigest(),
            }
        )
    )
    assert load_sidecar(body, tmp_path) is not None
    assert assess_record(body, tmp_path).digestible


def test_assess_record_no_sidecar_not_digestible(tmp_path):
    (tmp_path / "store" / "v1").mkdir(parents=True)
    (tmp_path / "by-name").mkdir()
    h = "b" * 64
    (tmp_path / "store" / "v1" / f"{h}.md").write_text(RECORD_BODY)
    link = tmp_path / "by-name" / "rec.md"
    link.symlink_to(Path("..") / "store" / "v1" / f"{h}.md")
    d = assess_record(link, tmp_path)
    assert not d.digestible
    assert d.source == "no-sidecar"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _legacy_repo(
    tmp_path: Path, *, body: str = "Reviewed body.\n"
) -> tuple[Path, Path]:
    repo = tmp_path / "ingests"
    store = repo / "store"
    by_name = repo / "by-name"
    store.mkdir(parents=True)
    by_name.mkdir()
    _git(repo, "init", "-q")
    bare_hash = "d" * 64
    record = store / f"{bare_hash}.md"
    record.write_bytes(
        (
            "---\n"
            "schema: anomalica/record/1\n"
            f"content_hash: sha256:{bare_hash}\n"
            "---\n"
            f"{body}"
        ).encode("utf-8")
    )
    link = by_name / "record.md"
    link.symlink_to(Path("..") / "store" / record.name)
    base = _commit(repo, "record")
    sidecar = store / f"{bare_hash}.review.json"
    sidecar.write_text(
        json.dumps(
            {
                "schema": "anomalica/review-coverage/1",
                "observed_coverage": 1.0,
                "digestible": True,
                "total_units": 1,
                "reviews": [
                    {
                        "by": "reviewer",
                        "at": "2026-09-15T00:00:00Z",
                        "spans": [],
                        "parent_commit": base,
                    }
                ],
            }
        )
    )
    _commit(repo, "review")
    return link, sidecar


def test_legacy_v1_accepts_only_clean_git_bound_body(tmp_path):
    record, _ = _legacy_repo(tmp_path)

    verdict = assess_record(record, tmp_path / "ingests")

    assert verdict.digestible
    assert verdict.source == "sidecar"


@pytest.mark.parametrize("change", ["body", "sidecar"])
def test_dirty_or_stale_legacy_evidence_fails_closed(tmp_path, change):
    record, sidecar = _legacy_repo(tmp_path)
    target = record.resolve() if change == "body" else sidecar
    target.write_bytes(target.read_bytes() + b"\n")

    assert not assess_record(record, tmp_path / "ingests").digestible


def test_legacy_parent_must_be_a_direct_parent_not_an_ancestor(tmp_path):
    record, sidecar = _legacy_repo(tmp_path)
    repo = tmp_path / "ingests"
    ancestor = _git(repo, "rev-parse", "HEAD~1")
    (repo / "intervening").write_text("commit")
    _commit(repo, "intervening commit")
    document = json.loads(sidecar.read_text())
    document["reviews"][-1]["parent_commit"] = ancestor
    document["note"] = "force a sidecar revision"
    sidecar.write_text(json.dumps(document))
    _commit(repo, "forged ancestor")

    assert not assess_record(record, repo).digestible


def test_sidecar_only_later_commit_invalidates_legacy_binding(tmp_path):
    record, sidecar = _legacy_repo(tmp_path)
    repo = tmp_path / "ingests"
    document = json.loads(sidecar.read_text())
    document["note"] = "later edit without a new review"
    sidecar.write_text(json.dumps(document))
    _commit(repo, "sidecar-only edit")

    assert not assess_record(record, repo).digestible


def test_delete_and_readd_does_not_restore_legacy_binding(tmp_path):
    record, sidecar = _legacy_repo(tmp_path)
    repo = tmp_path / "ingests"
    content = sidecar.read_bytes()
    sidecar.unlink()
    _git(repo, "add", ".")
    _commit(repo, "delete review")
    parent = _git(repo, "rev-parse", "HEAD")
    sidecar.write_bytes(content)
    document = json.loads(sidecar.read_text())
    document["reviews"][-1]["parent_commit"] = parent
    sidecar.write_text(json.dumps(document))
    _commit(repo, "re-add review")

    assert not assess_record(record, repo).digestible


def test_metadata_only_record_change_preserves_legacy_binding(tmp_path):
    record, _ = _legacy_repo(tmp_path)
    repo = tmp_path / "ingests"
    target = record.resolve()
    target.write_bytes(
        target.read_bytes().replace(b"schema:", b"title: Added\nschema:")
    )

    assert assess_record(record, repo).digestible


def test_merge_commit_accepts_either_actual_parent(tmp_path):
    repo = tmp_path / "ingests"
    store = repo / "store"
    store.mkdir(parents=True)
    _git(repo, "init", "-q")
    bare_hash = "e" * 64
    record = store / f"{bare_hash}.md"
    record.write_text(f"---\ncontent_hash: sha256:{bare_hash}\n---\nMerged body.\n")
    _commit(repo, "base")
    _git(repo, "checkout", "-qb", "other")
    (repo / "other").write_text("other")
    other = _commit(repo, "other")
    _git(repo, "checkout", "-q", "master")
    (repo / "main").write_text("main")
    _commit(repo, "main")
    _git(repo, "merge", "--no-ff", "--no-commit", "other")
    sidecar = store / f"{bare_hash}.review.json"
    sidecar.write_text(
        json.dumps(
            {
                "schema": "anomalica/review-coverage/1",
                "observed_coverage": 1.0,
                "digestible": True,
                "total_units": 1,
                "reviews": [{"at": "2026-09-15T00:00:00Z", "parent_commit": other}],
            }
        )
    )
    _commit(repo, "merge review")

    assert assess_record(record, repo).digestible


def test_review_commit_merged_unchanged_remains_latest_evidence(tmp_path):
    record, _ = _legacy_repo(tmp_path)
    repo = tmp_path / "ingests"
    review_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "review", review_commit)
    _git(repo, "checkout", "-q", "master")
    _git(repo, "reset", "--hard", "HEAD~1")
    (repo / "main-only").write_text("main")
    _commit(repo, "main diverges")
    _git(repo, "merge", "--no-ff", "-m", "merge review", "review")

    assert assess_record(record, repo).digestible


def test_record_rename_after_review_resolves_blob_by_content_hash(tmp_path):
    record, _ = _legacy_repo(tmp_path)
    repo = tmp_path / "ingests"
    old = record.resolve()
    renamed = old.with_name(old.name.replace(".md", ".v2.md"))
    old.rename(renamed)
    record.unlink()
    record.symlink_to(Path("..") / "store" / renamed.name)

    assert assess_record(record, repo).digestible


def test_legacy_v0_also_requires_and_accepts_exact_git_binding(tmp_path):
    record, sidecar = _legacy_repo(tmp_path)
    repo = tmp_path / "ingests"
    document = json.loads(sidecar.read_text())
    document["schema"] = "anomalica/review-coverage/0"
    document.pop("observed_coverage")
    document.pop("digestible")
    document.pop("total_units")
    document["reviews"][-1]["spans"] = [{"from": 5, "to": 5, "kind": "observed"}]
    document["reviews"][-1]["parent_commit"] = _git(repo, "rev-parse", "HEAD")
    sidecar.write_text(json.dumps(document))
    _commit(repo, "legacy v0 review")

    assert assess_record(record, repo).digestible
    sidecar.write_bytes(sidecar.read_bytes() + b"\n")
    assert not assess_record(record, repo).digestible


def test_v0_with_a_direct_hash_needs_no_git_but_remains_span_computed(tmp_path):
    record = _make_store(tmp_path, sidecar_at_root=True)
    sidecar_path = _sidecar_for(record)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["schema"] = "anomalica/review-coverage/0"
    sidecar["reviews"] = [{"spans": [{"from": 5, "to": 6, "kind": "observed"}]}]
    sidecar_path.write_text(json.dumps(sidecar))

    verdict = assess_record(record, tmp_path)

    assert verdict.digestible
    assert verdict.source == "computed"


@pytest.mark.parametrize(
    "span",
    [
        {"from": -1, "to": 6, "kind": "observed"},
        {"from": 5, "to": 4, "kind": "observed"},
        {"from": "5", "to": 6, "kind": "observed"},
        {"from": 5, "to": True, "kind": "observed"},
    ],
)
def test_v0_malformed_span_ranges_and_types_fail_closed(tmp_path, span):
    record = _make_store(tmp_path, sidecar_at_root=True)
    sidecar_path = _sidecar_for(record)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["schema"] = "anomalica/review-coverage/0"
    sidecar["reviews"] = [{"spans": [span]}]
    sidecar_path.write_text(json.dumps(sidecar))

    assert not assess_record(record, tmp_path).digestible


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_direct_hash_preserves_newlines_and_unicode_bytes(tmp_path, newline):
    record = _make_store(tmp_path, sidecar_at_root=True)
    text = RECORD_BODY.replace("First sentence.", "Cafe\u0301 sentence.").replace(
        "\n", newline
    )
    record.resolve().write_bytes(text.encode("utf-8"))
    sidecar_path = _sidecar_for(record)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["reviewed_body_sha256"] = (
        "sha256:" + hashlib.sha256(parsed_record_body(text).encode("utf-8")).hexdigest()
    )
    sidecar_path.write_text(json.dumps(sidecar))

    assert assess_record(record, tmp_path).digestible
    record.resolve().write_bytes(
        text.replace("Cafe\u0301", "Caf\u00e9").encode("utf-8")
    )
    assert not assess_record(record, tmp_path).digestible


def _sidecar_for(record: Path) -> Path:
    bare_hash = _BODY_SUFFIX_FOR_TEST.sub("", record.resolve().name)
    return record.resolve().parents[1] / f"{bare_hash}.review.json"


_BODY_SUFFIX_FOR_TEST = re.compile(r"(?:\.v\d+)?\.md$")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewed_body_sha256", "sha256:bad"),
        ("reviewed_body_sha256", 1),
        ("observed_coverage", -0.1),
        ("observed_coverage", 1.1),
        ("observed_coverage", "1"),
        ("total_units", 0),
        ("total_units", True),
        ("digestible", "true"),
    ],
)
def test_malformed_direct_binding_or_verdict_fails_closed(tmp_path, field, value):
    record = _make_store(tmp_path, sidecar_at_root=True)
    sidecar_path = _sidecar_for(record)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar[field] = value
    sidecar_path.write_text(json.dumps(sidecar))

    assert not assess_record(record, tmp_path).digestible


@pytest.mark.parametrize("review_at", [None, "2026-09-14T23:59:59Z", "invalid"])
def test_unresolved_review_carryover_fails_closed(tmp_path, review_at):
    record = _make_store(tmp_path, sidecar_at_root=True)
    text = (
        record.resolve()
        .read_bytes()
        .decode("utf-8")
        .replace("title: T", "title: T\nreview_carryover:\n  at: 2026-09-15T00:00:00Z")
    )
    record.resolve().write_bytes(text.encode("utf-8"))
    sidecar_path = _sidecar_for(record)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["reviewed_body_sha256"] = (
        "sha256:" + hashlib.sha256(parsed_record_body(text).encode("utf-8")).hexdigest()
    )
    sidecar["reviews"] = [] if review_at is None else [{"at": review_at}]
    sidecar_path.write_text(json.dumps(sidecar))

    assert not assess_record(record, tmp_path).digestible


def test_review_after_carryover_resolves_it(tmp_path):
    record = _make_store(tmp_path, sidecar_at_root=True)
    text = (
        record.resolve()
        .read_text()
        .replace("title: T", "title: T\nreview_carryover:\n  at: 2026-09-15T00:00:00Z")
    )
    record.resolve().write_text(text)
    sidecar_path = _sidecar_for(record)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["reviewed_body_sha256"] = (
        "sha256:" + hashlib.sha256(parsed_record_body(text).encode("utf-8")).hexdigest()
    )
    sidecar["reviews"] = [{"at": "2026-09-15T00:00:01Z"}]
    sidecar_path.write_text(json.dumps(sidecar))

    assert assess_record(record, tmp_path).digestible
