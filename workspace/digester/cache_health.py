"""Cache-prefix health: a canary for a cost regression with no other symptom.

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
