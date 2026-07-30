"""Post-digest health checks: conditions a successful exit code cannot see.

A run that exits 0 and writes a file looks identical to a good extraction.
Every check here turns one such silent success into a visible condition.

--- CACHE PREFIX ---

Prompt caching only pays when the STABLE part of a call comes before the variable
part. The claims pass assembles `prompt` (carrying the fixed node directory) and
then the chunk text, so the directory sits in the cacheable prefix and is read
back on every chunk rather than rewritten.

If that order is ever reversed - by a refactor, a new field appended in the wrong
place, a template edit - the prefix collapses and every call rewrites what it
used to read. Nothing else changes: the extraction is correct, the claims are the
same, every test passes. Only the cost characteristic moves, which is exactly the
class of failure that goes unnoticed.

`cache_read / cache_write` detects it directly. A healthy record reads back
roughly what it wrote (ratio near or above 1); a collapsed prefix drives the
ratio toward zero because nothing is ever read.

Measured baseline on books, 2026-07-31:

    Invisible College   1.23      Remote Viewing Secrets  0.96
    Conference Report   1.13      Hair of the Alien       0.81
    Messengers          1.03      Imminent                1.01

All near 1.0, which is itself a weak result - the cache roughly breaks even on
books rather than winning - and consistent with the CLI's own ~32K-per-call
scaffolding dominating the payload. The threshold here is set to catch a
COLLAPSE, not to police that weakness.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import yaml

# A prefix that has genuinely collapsed reads back almost nothing. The observed
# floor across real books is 0.81, so this sits well below it: it flags a broken
# prefix, never a merely inefficient one.
COLLAPSE_RATIO = 0.30

# Records that make one call have nothing to read back by construction - the
# prefix is written and never reused - so their ratio is legitimately 0.
MIN_CALLS = 3


def ratios(digests_dir: Path) -> list[dict]:
    """cache read/write ratio per digest, newest field set only."""
    out = []
    for f in sorted(digests_dir.glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text())
        except (OSError, yaml.YAMLError):
            continue
        usage = (d.get("ai_usage") or [{}])[0]
        t = usage.get("tokens") or {}
        read, write, calls = (
            t.get("cache_read"),
            t.get("cache_write"),
            t.get("calls"),
        )
        if not write:  # pre-dates the breakdown fields, or no caching happened
            continue
        out.append(
            {
                "digest": f.stem,
                "read": read or 0,
                "write": write,
                "calls": calls or 0,
                "ratio": (read or 0) / write,
            }
        )
    return out


def collapsed(digests_dir: Path, threshold: float = COLLAPSE_RATIO) -> list[dict]:
    """Digests whose cacheable prefix looks broken rather than merely small."""
    return [
        r
        for r in ratios(digests_dir)
        if r["ratio"] < threshold and r["calls"] >= MIN_CALLS
    ]


# --- YIELD FLOOR ---
#
# hair-of-the-alien produced 2 domain claims from 619KB, exited 0, wrote a digest,
# and sat in the corpus for days counted as a completed book - inflating every
# corpus figure quoted about it. Nothing flagged it because a successful exit and
# a written file are indistinguishable from a good extraction.
#
# Claim density varies legitimately by medium - spoken-word video runs 0.54-0.63
# claims/KB against 4.39 for a dense book - so an absolute floor would either miss
# the failure or condemn every transcript. A floor RELATIVE to the corpus median
# separates them: on 22 digests >=20KB the median is 2.37 claims/KB, the lowest
# genuine record is 0.54, and the failure is 0.00.
YIELD_FLOOR_FRACTION = 0.2
YIELD_MIN_KB = 20


def claim_yields(digests_dir: Path, store_dir: Path) -> list[dict]:
    """claims-per-source-KB per digest, for records large enough to judge."""
    out = []
    for f in sorted(digests_dir.glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text())
        except (OSError, yaml.YAMLError):
            continue
        h = ((d.get("record") or {}).get("content_hash") or "").split(":")[-1]
        src = 0
        for c in (
            store_dir / f"{h}.v2.md",
            store_dir / f"{h}.md",
            store_dir / "v1" / f"{h}.v2.md",
            store_dir / "v1" / f"{h}.md",
        ):
            if c.exists():
                src = c.stat().st_size
                break
        kb = src / 1000
        if kb < YIELD_MIN_KB:
            continue
        n = len((d.get("domain_claims") or [])) + len(
            (d.get("infrastructure_claims") or [])
        )
        out.append({"digest": f.stem, "kb": kb, "claims": n, "per_kb": n / kb})
    return out


def low_yield(digests_dir: Path, store_dir: Path) -> list[dict]:
    """Digests whose claim yield is so far below the corpus norm that the
    extraction probably failed despite exiting successfully."""
    rows = claim_yields(digests_dir, store_dir)
    if len(rows) < 5:
        return []
    med = statistics.median(r["per_kb"] for r in rows)
    return [r for r in rows if r["per_kb"] < med * YIELD_FLOOR_FRACTION]


# --- PRE-DIGEST SURVIVAL ---
#
# hair-of-the-alien reaches the model as 1,023 characters of a 88,384-character
# body: four irrelevant regions remove 98.8% of it. The extraction then behaves
# correctly on the kilobyte it is given, exits 0, and writes a digest that reads
# like a completed book.
#
# The yield floor catches that case only because it is extreme. A book marked 40%
# irrelevant would produce a plausible claim count and never trip it, while
# silently omitting two fifths of the source. This measures the thing directly:
# how much of the body survives materialise. Across 17 books the marked ones sit
# at 93.5-97% and the unmarked at 98-100%, so a real apparatus pass is visible
# and harmless here; anything below half is not apparatus.
SURVIVAL_FLOOR = 0.5


def pre_digest_survival(record_md: Path) -> float | None:
    """Fraction of a record's body that survives into the pre-digest."""
    from anomalica_common.pre_digest import materialise

    from digester.record_parser import parse_record

    from anomalica_common.pre_digest import strip_word_timestamps

    try:
        body = parse_record(record_md.resolve().read_text(errors="replace")).body
    except (OSError, ValueError):
        return None
    if not body:
        return None
    # Measure against the body with WORD TIMESTAMPS already removed. A record/2
    # transcript is ~65% {{t:...}} tokens by character, and materialise strips
    # them correctly - so a raw ratio puts every transcript at ~30% and a flat
    # floor flags 129 records that are all fine. Comparing like with like isolates
    # what the ANNOTATIONS remove from what the transcript FORMAT removes.
    baseline = strip_word_timestamps(body)
    if not baseline:
        return None
    return len(materialise(body)) / len(baseline)


def over_marked(records_dir: Path, floor: float = SURVIVAL_FLOOR) -> list[dict]:
    """Records whose annotations remove so much body that a digest built from
    them would misrepresent the source."""
    out = []
    for p in sorted(records_dir.glob("*.md")):
        frac = pre_digest_survival(p)
        if frac is not None and frac < floor:
            out.append({"record": p.name, "survives": round(frac, 4)})
    return out
