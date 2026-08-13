#!/usr/bin/env python3
"""Recovery re-digest: re-extract the dirty (deprecated-type, pre-taxonomy sonnet)
digests on OPUS, overwriting them clean. Sequential + paced - gentle on the
shared fleet while Mark is away. Smallest-first, so the cheap records finish
before the two big ebooks and an early halt loses the least.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path("/home/mark/repos/anomalica")
STORE = ROOT / "ingests/store"
COMMON = ROOT / "anomalica-common/src"
WORKSPACE = ROOT / "digester/workspace"
DEP = {"matter", "concept", "programme", "investigation", "pattern"}
PAUSE = 20  # gentle inter-record pause


def body(h: str):
    for c in (
        STORE / f"{h}.v2.md",
        STORE / f"{h}.md",
        STORE / "v1" / f"{h}.v2.md",
        STORE / "v1" / f"{h}.md",
    ):
        if c.exists():
            return c
    return None


def dirty_set():
    out = []
    for d in sorted(glob.glob(str(ROOT / "digests/records/*.yaml"))):
        # Scope cap (master): hold the two big ebooks for a supervised run when
        # Mark's back to watch fleet contention - they're 3.5h of the 5.9h.
        if "ebook" in Path(d).stem:
            print(f"HELD (ebook, supervised later): {Path(d).stem}", flush=True)
            continue
        doc = yaml.safe_load(open(d).read())
        dep = sum(1 for n in doc.get("nodes", []) if n.get("type") in DEP)
        if dep == 0:
            continue
        h = (doc.get("record", {}).get("content_hash") or "").split(":")[-1]
        bf = body(h) if h else None
        if not bf:
            print(f"SKIP {Path(d).stem}: no body for {h!r}", flush=True)
            continue
        out.append((Path(d), bf, len(bf.read_text()), dep))
    return sorted(out, key=lambda r: r[2])  # smallest first


def dead_types(digest_path: Path) -> dict:
    doc = yaml.safe_load(open(digest_path).read())
    bad: dict = {}
    for n in doc.get("nodes", []):
        if n.get("type") in DEP:
            bad[n["type"]] = bad.get(n["type"], 0) + 1
    return bad


def main() -> int:
    items = dirty_set()
    print(
        f"=== RECOVERY RE-DIGEST: {len(items)} dirty records on OPUS (smallest first) ===",
        flush=True,
    )
    results = []
    for i, (digest, bf, chars, dep0) in enumerate(items, 1):
        print(
            f"\n[{i}/{len(items)}] {digest.stem}  ({chars:,} chars, was {dep0} dead-type)",
            flush=True,
        )
        t0 = time.time()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "digester.cli",
                "extract",
                str(bf),
                "-o",
                str(digest),
                "--model",
                "opus",
            ],
            cwd=str(WORKSPACE),
            env={**os.environ, "PYTHONPATH": str(COMMON)},
            capture_output=True,
            text=True,
        )
        secs = time.time() - t0
        if proc.returncode != 0:
            print(f"  FAIL rc={proc.returncode}\n  {proc.stderr[-600:]}", flush=True)
            results.append((digest.stem, "FAIL", None))
            continue
        bad = dead_types(digest)
        print(
            f"  done {secs:.0f}s -> {'!! STILL DIRTY ' + str(bad) if bad else 'clean (0 dead-type)'}",
            flush=True,
        )
        results.append((digest.stem, "ok", bad))
        if i < len(items):
            time.sleep(PAUSE)

    print("\n=== SUMMARY ===", flush=True)
    ok = sum(1 for _, s, _ in results if s == "ok")
    failed = [r[0] for r in results if r[1] == "FAIL"]
    still_dirty = [r[0] for r in results if r[1] == "ok" and r[2]]
    print(
        f"re-digested: {ok}/{len(items)} | failed: {failed or 'none'} | "
        f"still-dirty: {still_dirty or 'NONE'}",
        flush=True,
    )
    print("RECOVERY DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
