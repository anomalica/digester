"""The digester's review-gate WRAPPERS (the digestibility rule itself is tested
in anomalica-common). Covers load_sidecar's store/ vs store/v1/ resolution and
assess_record's end-to-end file -> verdict path."""

from pathlib import Path

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
    }
    import json

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
    import json

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
