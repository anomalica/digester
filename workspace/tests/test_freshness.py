"""Freshness is decided by the recomputed hash, not by the prep version.

A PREP_VERSION bump changes materialise's output only for records carrying the
markers it touched. Reporting on the version before recomputing flagged 86 of
108 digests after the 6 -> 7 bump, 53 of them with text that hashed identically
under the new prep, and buried the 32 whose text had actually moved.
"""

from __future__ import annotations

import yaml
from anomalica_common.pre_digest import PREP_VERSION, materialise, pre_digest_hash

from digester import health
from digester.record_parser import parse_record

HASH = "a" * 64


def _record(body: str, hash_: str = HASH, supersedes: str | None = None) -> str:
    sup = f"supersedes: {supersedes}\n" if supersedes else ""
    return (
        f"---\ncontent_hash: sha256:{hash_}\n{sup}schema: anomalica/record/1\n---\n\n"
        f"{body}\n"
    )


def _hash(record_text: str) -> str:
    return pre_digest_hash(materialise(parse_record(record_text).body))


def _corpus(tmp_path, monkeypatch, *, digest_sha, prep, store_body, by_name_body=None):
    """A one-record ingests tree plus one digest. Returns (digests, by-name)."""
    monkeypatch.setattr(health, "_SURVIVAL_CACHE", tmp_path / "cache.json")
    ingests = tmp_path / "ingests"
    store, by_name = ingests / "store", ingests / "by-name"
    store.mkdir(parents=True)
    by_name.mkdir()
    (store / f"{HASH}.md").write_text(_record(store_body))
    link = by_name / "slug.md"
    if by_name_body is None:
        link.symlink_to(f"../store/{HASH}.md")
    else:
        link.write_text(_record(by_name_body))
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / "slug.yaml").write_text(
        yaml.safe_dump(
            {
                "record": {"content_hash": f"sha256:{HASH}"},
                "pre_digest": {"sha256": digest_sha, "prep_version": prep},
            }
        )
    )
    return digests, by_name


def test_an_older_prep_whose_text_still_hashes_the_same_is_fresh(tmp_path, monkeypatch):
    body = "The witness saw a light.\nIt moved against the wind."
    d, r = _corpus(
        tmp_path,
        monkeypatch,
        digest_sha=_hash(_record(body)),
        prep=PREP_VERSION - 1,
        store_body=body,
    )
    assert health.pre_digest_freshness(d, r) == []


def test_an_older_prep_whose_text_moved_is_reported_under_the_version(
    tmp_path, monkeypatch
):
    d, r = _corpus(
        tmp_path,
        monkeypatch,
        digest_sha=_hash(_record("The witness saw a light.\nIt moved.")),
        prep=PREP_VERSION - 1,
        store_body="The witness saw two lights.\nThey moved.",
    )
    found = health.pre_digest_freshness(d, r)
    assert [f["issue"] for f in found] == ["prep_version"]
    assert found[0]["record"] == r / "slug.md"


def test_a_current_prep_whose_text_moved_is_record_changed(tmp_path, monkeypatch):
    d, r = _corpus(
        tmp_path,
        monkeypatch,
        digest_sha=_hash(_record("The witness saw a light.\nIt moved.")),
        prep=PREP_VERSION,
        store_body="The witness saw two lights.\nThey moved.",
    )
    assert [f["issue"] for f in health.pre_digest_freshness(d, r)] == ["record_changed"]


def test_the_store_body_is_compared_when_by_name_holds_a_stale_copy(
    tmp_path, monkeypatch
):
    """A by-name entry that is a regular file, not a symlink, can lag the store."""
    old = "The witness saw a light.\nIt moved."
    d, r = _corpus(
        tmp_path,
        monkeypatch,
        digest_sha=_hash(_record(old)),
        prep=PREP_VERSION,
        store_body="The witness saw two lights.\nThey moved.",
        by_name_body=old,
    )
    found = health.pre_digest_freshness(d, r)
    assert [f["issue"] for f in found] == ["record_changed"]
    assert found[0]["record"] == r / "slug.md", (
        "the by-name path still names the record"
    )


def test_a_digest_of_a_superseded_record_resolves_to_its_replacement(
    tmp_path, monkeypatch
):
    """A re-ingest retires the old hash to store/v1; the digest must not read as orphaned."""
    monkeypatch.setattr(health, "_SURVIVAL_CACHE", tmp_path / "cache.json")
    old_hash, new_hash = "b" * 64, "c" * 64
    ingests = tmp_path / "ingests"
    store, by_name = ingests / "store", ingests / "by-name"
    (store / "v1").mkdir(parents=True)
    by_name.mkdir()
    old = _record("The witness saw a light.\nIt moved.", old_hash)
    (store / "v1" / f"{old_hash}.md").write_text(old)
    (store / f"{new_hash}.md").write_text(
        _record(
            "The witness saw two lights.\nThey moved.", new_hash, supersedes=old_hash
        )
    )
    (by_name / "slug.md").symlink_to(f"../store/{new_hash}.md")
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / "slug.yaml").write_text(
        yaml.safe_dump(
            {
                "record": {"content_hash": f"sha256:{old_hash}"},
                "pre_digest": {"sha256": _hash(old), "prep_version": PREP_VERSION},
            }
        )
    )
    found = health.pre_digest_freshness(digests, by_name)
    assert [f["issue"] for f in found] == ["record_changed"]
    assert found[0]["record"] == by_name / "slug.md"
