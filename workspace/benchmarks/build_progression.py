#!/usr/bin/env python3
"""Loop-by-loop extraction progression from the per-model stdout traces.

run_models.py saves each model's full stdout (the on_progress iteration lines
and the TRACE_JSON per-call usage line). This parses them into, per model, an
ordered list of extraction loops - each loop's new items and the call's cost -
and plots cumulative items found against cumulative cost. Generation dynamics
only; this does not grade quality.

  python benchmarks/build_progression.py            # all models -> json + png
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = HERE / "navy-pilots" / "model-runs"

NODE_ITER = re.compile(r"nodes iter \d+: \+(\d+) nodes")
CLAIM_ITER = re.compile(r"claims iter \d+: \+(\d+) claims")
TRACE = re.compile(r"TRACE_JSON: (\[.*\])")


def parse(path: Path):
    text = path.read_text()
    events = []  # (pass, new_items) in call order
    trace = []
    for line in text.splitlines():
        m = NODE_ITER.search(line)
        if m:
            events.append(("nodes", int(m.group(1))))
            continue
        m = CLAIM_ITER.search(line)
        if m:
            events.append(("claims", int(m.group(1))))
            continue
        m = TRACE.search(line)
        if m:
            trace = json.loads(m.group(1))
    return events, trace


def progression(events, trace):
    recs = []
    cum_nodes = cum_claims = 0
    cum_cost = 0.0
    cum_in = cum_out = 0
    for i in range(max(len(events), len(trace))):
        kind, new = events[i] if i < len(events) else (None, 0)
        tr = trace[i] if i < len(trace) else {}
        if kind == "nodes":
            cum_nodes += new
        elif kind == "claims":
            cum_claims += new
        cum_cost += float(tr.get("cost_usd") or 0.0)
        cum_in += int(tr.get("input_tokens") or 0)
        cum_out += int(tr.get("output_tokens") or 0)
        recs.append(
            {
                "loop": i + 1,
                "pass": kind,
                "new": new,
                "cum_nodes": cum_nodes,
                "cum_claims": cum_claims,
                "cum_items": cum_nodes + cum_claims,
                "call_cost": float(tr.get("cost_usd") or 0.0),
                "cum_cost": round(cum_cost, 6),
                "cum_in": cum_in,
                "cum_out": cum_out,
            }
        )
    return recs


def main() -> int:
    data = {}
    for p in sorted(RUNS.glob("navy-pilots.*.stdout.txt")):
        stem = p.name[len("navy-pilots.") : -len(".stdout.txt")]
        # only models that produced a valid digest (exclude failed partials)
        if not (RUNS / f"navy-pilots.{stem}.yaml").exists():
            continue
        model = stem.replace("_", "/", 1)
        events, trace = parse(p)
        if not events:
            continue
        recs = progression(events, trace)
        data[model] = {
            "loops": recs,
            "n_loops": len(recs),
            "n_node_loops": sum(1 for e in events if e[0] == "nodes"),
            "n_claim_loops": sum(1 for e in events if e[0] == "claims"),
            "total_items": recs[-1]["cum_items"],
            "total_cost": recs[-1]["cum_cost"],
        }

    out_json = RUNS / "progression.json"
    out_json.write_text(json.dumps(data, indent=2))

    # plot: cumulative items vs cumulative cost, one line per model
    fig, ax = plt.subplots(figsize=(13, 8))
    for model, d in sorted(data.items(), key=lambda kv: -kv[1]["total_cost"]):
        xs = [r["cum_cost"] for r in d["loops"]]
        ys = [r["cum_items"] for r in d["loops"]]
        ax.plot(xs, ys, marker="o", ms=3, lw=1, alpha=0.8)
        ax.annotate(
            model.split("/")[-1],
            (xs[-1], ys[-1]),
            fontsize=7,
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
        )
    ax.set_xscale("log")
    ax.set_xlabel("cumulative cost (USD, log scale)")
    ax.set_ylabel("cumulative items found (nodes + claims)")
    ax.set_title("Extraction progression per loop: items found vs cost (navy-pilots)")
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    png = RUNS / "progression.png"
    fig.savefig(png, dpi=130)

    print(f"{len(data)} models with loop traces")
    for model, d in sorted(data.items(), key=lambda kv: -kv[1]["total_items"]):
        print(
            f"  {model:40s} loops={d['n_loops']:>2} "
            f"(nodes {d['n_node_loops']}, claims {d['n_claim_loops']})  "
            f"items={d['total_items']:>3}  ${d['total_cost']:.4f}"
        )
    print(f"\njson: {out_json}\npng:  {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
