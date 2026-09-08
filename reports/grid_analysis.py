#!/usr/bin/env python3
"""Read the grid's arms and say what they establish - or that they do not.

The grid runs several IDENTICAL arms per model so the spread within a model can
be measured at the same time as the difference between models. That is the only
way a difference means anything: a gap smaller than the within-model spread is
the spread.

    python3 reports/grid_analysis.py [--label-prefix grid-]
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workspace"))
sys.path.insert(0, "/home/mark/repos/anomalica/anomalica-common/src")

from digester.digest_store import split_variant_stem  # noqa: E402
from digester.eval import grade_digest  # noqa: E402
from digester.record_parser import parse_record  # noqa: E402

BY = Path("/home/mark/repos/anomalica/ingests/by-name")
VARIANTS = Path("/home/mark/repos/anomalica/digests/variants")


def arms(prefix: str) -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for f in sorted(VARIANTS.glob(f"*/*.{'*'}.yaml")):
        model, sha, label = split_variant_stem(f.stem)
        if not label.startswith(prefix):
            continue
        out[f.parent.name][model].append({"path": f, "prompt": sha, "label": label})
    return out


def body_for(record_dir: str) -> str | None:
    hits = sorted(BY.glob(record_dir + ".md")) + sorted(BY.glob(record_dir + ".v*.md"))
    return parse_record(hits[0].read_text(errors="replace")).body if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label-prefix", default="grid-")
    args = ap.parse_args()

    grid = arms(args.label_prefix)
    if not grid:
        print(f"No variant carries a '{args.label_prefix}*' run label yet.")
        return 1

    for record, by_model in sorted(grid.items()):
        body = body_for(record)
        if body is None:
            print(f"{record}: record not on disk")
            continue
        print(f"\n=== {record[:70]}")
        scored: dict[str, list[float]] = {}
        configs, prompts = set(), set()
        for model, entries in sorted(by_model.items()):
            recalls = []
            for e in entries:
                d = yaml.safe_load(e["path"].read_text())
                g = grade_digest(body, d)
                recalls.append(g["recall"])
                prompts.add(e["prompt"])
                configs.add((d.get("extraction_config") or {}).get("config", "-"))
                print(
                    f"  {model:8} {e['label']:8} recall {g['recall']:.3f}  "
                    f"fidelity {g['quote_fidelity']:.3f}  claims {g['claims']:4}"
                )
            scored[model] = [r for r in recalls if r is not None]

        if len(prompts) > 1 or len(configs) > 1:
            print(f"  MIXED CONFIGURATION: prompts {prompts}, configs {configs}")
            print("  These arms are not comparable. Stop here.")
            continue

        print()
        floors = []
        for model, rs in sorted(scored.items()):
            if len(rs) < 2:
                print(f"  {model:8} one arm only - contributes no spread")
                continue
            spread = max(rs) - min(rs)
            floors.append(spread)
            print(
                f"  {model:8} {len(rs)} arms  mean {st.mean(rs):.3f}  "
                f"spread {spread:.3f}  sd {st.stdev(rs):.4f}"
            )
        if not floors:
            print("  No repeated arm yet: no floor, so no difference can be read.")
            continue
        floor = max(floors)
        print(f"\n  FLOOR (largest within-model spread): {floor:.3f}")

        names = sorted(scored)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                ra, rb = scored[a], scored[b]
                if not ra or not rb:
                    continue
                gap = st.mean(ra) - st.mean(rb)
                verdict = (
                    "INSIDE THE FLOOR - says nothing"
                    if abs(gap) <= floor
                    else "outside the floor on this record"
                )
                print(f"  {a} - {b}: {gap:+.3f}  {verdict}")
        print(
            "  A gap outside the floor on ONE record is still one record. "
            "It needs the same sign on the other before it is a finding."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
