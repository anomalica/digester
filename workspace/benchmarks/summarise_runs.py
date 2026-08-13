#!/usr/bin/env python3
"""Build a comparison table from the per-model digest YAMLs in
navy-pilots/model-runs/. Reads each digest directly (not the manifest), so it
reflects whatever has been produced so far. Reports per model: node count,
domain/infra claim counts, the OpenRouter cost and token usage recorded in the
digest's ai_usage block, and claims-per-dollar. Generation metrics only - this
does not grade quality (that is the separate scoring step)."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
RUNS = HERE / "navy-pilots" / "model-runs"


def load(path: Path) -> dict:
    d = yaml.safe_load(path.read_text())
    usage = (d.get("ai_usage") or [{}])[-1]
    tok = usage.get("tokens") or {}
    nodes = d.get("nodes")
    n_nodes = (
        len(nodes)
        if isinstance(nodes, list)
        else sum(len(v) for v in nodes.values() if isinstance(v, list))
        if isinstance(nodes, dict)
        else 0
    )
    domain = len(d.get("domain_claims") or [])
    infra = len(d.get("infrastructure_claims") or [])
    cost = usage.get("notional_cost_usd") or 0.0
    return {
        "model": d.get("model", path.stem),
        "nodes": n_nodes,
        "domain": domain,
        "infra": infra,
        "claims": domain + infra,
        "in": (tok.get("input") or 0),
        "out": (tok.get("output") or 0),
        "cost": cost,
        "claims_per_usd": (domain + infra) / cost if cost else None,
    }


def main() -> int:
    rows = [load(p) for p in sorted(RUNS.glob("navy-pilots.*.yaml"))]
    rows.sort(key=lambda r: r["cost"])
    total = sum(r["cost"] for r in rows)

    h = f"{'model':40} {'nodes':>5} {'claims':>6} {'in':>7} {'out':>7} {'cost$':>8} {'cl/$':>7}"
    print(h)
    print("-" * len(h))
    for r in rows:
        cpd = f"{r['claims_per_usd']:.0f}" if r["claims_per_usd"] else "-"
        print(
            f"{r['model']:40} {r['nodes']:>5} {r['claims']:>6} "
            f"{r['in']:>7} {r['out']:>7} {r['cost']:>8.4f} {cpd:>7}"
        )
    print("-" * len(h))
    print(f"{len(rows)} models produced output | total recorded cost ${total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
