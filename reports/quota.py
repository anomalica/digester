#!/usr/bin/env python3
"""Both plan windows in one cached read: `weekly session`, or `unknown unknown`.

Two things this exists to prevent, both met on 2026-09-08.

A FAILED READ IS NOT A FULL WINDOW. The first version returned 100 when the
call failed, so an HTTP 429 - which the run itself provokes by asking too often
- was indistinguishable from an exhausted allowance. The session-window wait
would then pause a grid that had plenty of headroom, re-read, get 429 again,
and burn the night in ten-minute sleeps. `unknown` is a third answer and the
caller decides what to do with it.

ASKING COSTS. The quota endpoint rate-limits, and between the grid's own checks
and a human polling it while watching, it returned 429 within the hour. One
call answers both windows, and the answer is cached for five minutes.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CACHE = Path("/tmp/anomalica-grid-quota.json")
MAX_AGE_S = 300
SCHEDULER = "/home/mark/repos/anomalica/scheduler"


def _cached() -> dict | None:
    try:
        blob = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return None
    return blob if time.time() - blob.get("at", 0) < MAX_AGE_S else None


def _fetch() -> dict | None:
    sys.path.insert(0, SCHEDULER)
    try:
        from backend import usage
    except ImportError:
        return None
    for attempt in range(3):
        got = usage._claude_usage({})
        windows = {w["name"]: int(w["used"]) for w in (got.get("windows") or [])}
        if windows:
            return windows
        if attempt < 2:
            time.sleep(20)
    return None


def main() -> int:
    blob = _cached()
    windows = blob.get("windows") if blob else None
    if windows is None:
        windows = _fetch()
        if windows is not None:
            try:
                CACHE.write_text(json.dumps({"at": time.time(), "windows": windows}))
            except OSError:
                pass
    if not windows:
        print("unknown unknown")
        return 0
    print(f"{windows.get('weekly', 'unknown')} {windows.get('session', 'unknown')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
