#!/usr/bin/env python3
"""Compute the metrics trajectory across tuning stages for both models."""

import json
import sys

import yaml

sys.path.insert(0, "/home/mark/repos/anomalica/digester/workspace/benchmarks")
sys.path.insert(0, "/tmp/digester-exp")
import assess  # noqa: E402
import runner  # noqa: E402

GOLDEN = yaml.safe_load(
    open(
        "/home/mark/repos/anomalica/digester/workspace/benchmarks/navy-pilots/golden.yaml"
    )
)

OLD = "2021-05-17-video-navy-pilots-describe-encounters-with-ufos"
STAGES = {
    "haiku": [
        ("baseline", f"{OLD}.haiku.yaml"),
        ("attempt 1 (units/Q&A/durability)", "navy.haiku.v2.yaml"),
        ("round 1 (orientation-grounded)", "navy.haiku.r1.yaml"),
        ("round 2 (assertion + node sweep)", "navy.haiku.r2.yaml"),
        ("round 3 (haiku hard rules) FINAL", "navy.haiku.r3.yaml"),
    ],
    "sonnet": [
        ("baseline", f"{OLD}.sonnet.yaml"),
        ("attempt 1 (units/Q&A/durability)", "navy.sonnet.v2.yaml"),
        ("round 1 (orientation-grounded)", "navy.sonnet.r1.yaml"),
        ("round 2 (assertion + node sweep) FINAL", "navy.sonnet.r2.yaml"),
    ],
}


def metrics(path):
    d = yaml.safe_load(open(path))
    claims = d.get("domain_claims", []) or []
    found = {}
    for n in d.get("nodes", []) or []:
        t = n.get("type") or "unknown"
        found.setdefault(t, []).append({"name": n.get("name", "")})
    sc = runner.score(GOLDEN, {"found": found})
    tot, _ = assess.assess(path)
    return {
        "claims": len(claims),
        "node_recall": sc["recall"],
        "node_precision": sc["precision"],
        "imperial": tot["imperial"],
        "abbrev_unit": tot["abbrev-unit"],
        "reporting_anchor": tot["reporting-anchor"],
        "american": tot["american"],
        "compound": tot["compound"],
        "vague": tot["vague"],
    }


out = {}
for model, stages in STAGES.items():
    out[model] = []
    for label, fname in stages:
        try:
            m = metrics(fname)
            m["stage"] = label
            out[model].append(m)
        except FileNotFoundError:
            pass

json.dump(out, open("/tmp/digester-exp/progression.json", "w"), indent=2)
# print a readable table
for model, rows in out.items():
    print(f"\n=== {model} ===")
    print(
        f"{'stage':<38} {'claims':>6} {'noderec':>7} {'imp':>4} {'rptA':>5} {'cmpd':>5} {'vague':>6} {'amEng':>6}"
    )
    for r in rows:
        print(
            f"{r['stage']:<38} {r['claims']:>6} {str(r['node_recall']):>7} "
            f"{r['imperial']:>4} {r['reporting_anchor']:>5} {r['compound']:>5} {r['vague']:>6} {r['american']:>6}"
        )
