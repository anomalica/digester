"""Refresh a digest's record block from its record's CURRENT frontmatter.

Deterministic. No model calls, no allowance, no dollars. It exists because the
record block is COPIED from frontmatter and never fed to the model, so a
frontmatter correction cannot change a single claim - re-extracting to pick one
up would spend model calls recomputing an identical result.

That makes it the only way to land `copyright_status` on the existing corpus.
Until a digest carries it the graph reads the record as not distributable, which
is the correct failure direction and also means every existing record is
un-publishable. Re-digesting ~100 records to copy one string would be one of the
more expensive no-ops available.

WHAT IT REFUSES, and why it is a refusal rather than a warning: `speakers` is the
one frontmatter field whose content ALSO appears in the body, as
`<!-- speaker: -->` annotations the model does see. Refreshing a roster whose
body annotations were not correspondingly edited produces a digest whose roster
reads one spelling while its claims reference a node built from another -
internally consistent-looking from either half and wrong. A record whose roster
would change is skipped and reported, never silently half-updated.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import yaml

from anomalica_common.digest.yaml_format import _yaml_dump

from digester.health import _Loader, _records_by_hash, load_digests

# Copied from frontmatter, never sent to the model, therefore refreshable.
REFRESHABLE = (
    "title",
    "publisher",
    "duration",
    "release",
    "provenance",
    "classification",
    "supersedes",
    "speakers",
    "pages",
    "fetched_url",
    "description",
)


def _desired(md: dict) -> dict:
    """The record-block values a fresh extraction would emit from this record."""
    out = {k: v for k in REFRESHABLE if (v := md.get(k))}
    cp = md.get("copyright")
    if isinstance(cp, dict) and cp.get("status"):
        out["copyright_status"] = cp["status"]
    return out


def plan(digests_dir: Path, records_dir: Path) -> list[dict]:
    """What would change, per digest. Nothing is written."""
    by_hash = _records_by_hash(records_dir)
    out = []
    for stem, d in load_digests(digests_dir):
        rec_block = d.get("record") or {}
        h = str(rec_block.get("content_hash") or "").split(":")[-1]
        rec = by_hash.get(h)
        if rec is None:
            out.append({"digest": stem, "skip": "no record for content_hash"})
            continue
        raw = rec.read_text(errors="replace")
        try:
            md = yaml.load(raw.split("---", 2)[1], Loader=_Loader) or {}
        except (yaml.YAMLError, IndexError):
            out.append({"digest": stem, "skip": "unparseable frontmatter"})
            continue
        want = _desired(md)
        changes = {k: v for k, v in want.items() if rec_block.get(k) != v}
        if not changes:
            continue
        if "speakers" in changes:
            out.append(
                {
                    "digest": stem,
                    "skip": "speakers roster differs - body annotations may not "
                    "match; re-digest rather than refresh",
                }
            )
            continue
        out.append(
            {"digest": stem, "path": digests_dir / f"{stem}.yaml", "changes": changes}
        )
    return out


def _scalar(v) -> str:
    """One-line YAML for a value.

    NOT safe_dump: dumping a bare scalar emits a `...` document-end marker, which
    spliced into the line and made 68 digests unparseable - "did not find expected
    <document start>". Caught by validating that the files still PARSE; the diff
    size looked perfect.
    """
    text = yaml.safe_dump(
        v, default_flow_style=True, allow_unicode=True, width=10**6
    ).strip()
    if text.endswith("..."):
        text = text[:-3].strip()
    return text


def _splice(raw: str, changes: dict, curation: dict) -> str:
    """Edit ONLY the record block and append curation, leaving every other byte.

    A YAML round-trip reformats the whole file - 536k lines of diff across 68
    digests, whichever dumper is used - which buries the one changed block, makes
    the edit unreviewable, and pollutes blame on a shared repo forever. The
    content was identical both times; the diff was still the wrong artefact.
    """
    lines = raw.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "record:")
    except StopIteration:
        return raw
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i] and not lines[i].startswith((" ", "\t", "-"))
        ),
        len(lines),
    )
    block = lines[start + 1 : end]
    seen = set()
    for i, ln in enumerate(block):
        key = ln.split(":", 1)[0].strip()
        if key in changes:
            block[i] = f"  {key}: {_scalar(changes[key])}"
            seen.add(key)
    for k, v in changes.items():
        if k not in seen:
            block.append(f"  {k}: {_scalar(v)}")
    out = lines[: start + 1] + block + lines[end:]
    text = "\n".join(out)
    cur = yaml.safe_dump({"curation": [curation]}, sort_keys=False, allow_unicode=True)
    return text.rstrip("\n") + "\n" + cur if "\ncuration:" not in text else text


def apply(rows: list[dict], now: str | None = None) -> int:
    """Write the planned changes. Records a curation entry per digest."""
    stamp = now or datetime.datetime.now(datetime.UTC).isoformat()
    n = 0
    for r in rows:
        if r.get("skip") or not r.get("changes"):
            continue
        p: Path = r["path"]
        # The artefact says a non-model process touched it. Absent curation means
        # model output as emitted, so a silent rewrite would make this digest
        # indistinguishable from one the model produced with these values.
        curation = {
            "at": stamp,
            "by": "metadata-refresh",
            "changed": ["record"],
            "why": "Record block refreshed from current record frontmatter. "
            "No re-extraction; claims and nodes untouched.",
        }
        raw = p.read_text()
        text = _splice(raw, r["changes"], curation)
        # SELF-VALIDATING. The splice keeps the diff to a few lines instead of
        # reformatting the file, but it is text surgery on YAML and it has been
        # wrong twice - once emitting a `...` document-end marker into a line,
        # once on a block mapping it mis-bounded. So the result is PARSED, and a
        # file that does not survive falls back to a full round-trip, which is
        # ugly in diff and correct by construction. A writer that can emit an
        # unparseable artefact is not acceptable at any diff size.
        try:
            check = yaml.load(text, Loader=_Loader)
            if not isinstance(check, dict) or not check.get("record"):
                raise ValueError("spliced result lost its record block")
        except (yaml.YAMLError, ValueError):
            d = yaml.load(raw, Loader=_Loader)
            d.setdefault("record", {}).update(r["changes"])
            d.setdefault("curation", []).append(curation)
            text = _yaml_dump(d)
            yaml.load(text, Loader=_Loader)  # fail loudly rather than write junk
        p.write_text(text)
        n += 1
    return n
