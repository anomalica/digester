#!/usr/bin/env python3
"""Three-way extraction model comparison: opus vs sonnet vs haiku.

Runs the SAME production two-pass extraction (NODES_PROMPT_V2 default, no
per-model prompt override) on one record for each model, so the model is the
only variable, then grades:
  - node recall/precision (deterministic, runner.py vs golden.yaml)
  - claim recall (LLM judge via grade_claim_recall.py - subscription transport)
  - claim count
  - wall-clock seconds per model

All runs go through the digester's default transport (the Claude subscription),
so this spends no API dollars - only shared-fleet rate limits. Usage:
  python benchmarks/model_compare.py [model1 model2 ...]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
ANOM = WORKSPACE.parent.parent
RECORD = (
    ANOM
    / "ingests/records/2021-05-17-video-navy-pilots-describe-encounters-with-ufos.md"
)
GOLDEN = HERE / "navy-pilots" / "golden.yaml"
GT = HERE / "ground-truth" / "2021-05-17-navy-pilots.gt.yaml"
OUT_DIR = Path("/tmp/model-compare")

MODELS = sys.argv[1:] or ["haiku", "sonnet", "opus"]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def extract(model: str, out: Path) -> float:
    t0 = time.time()
    proc = run(
        [
            sys.executable,
            "-m",
            "digester.cli",
            "extract",
            str(RECORD),
            "--model",
            model,
            "-o",
            str(out),
        ],
        cwd=str(WORKSPACE),
        timeout=3600,
    )
    elapsed = time.time() - t0
    if proc.returncode != 0 or not out.exists():
        print(
            f"  EXTRACT FAILED ({model}):\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
        )
        return -1.0
    return elapsed


def node_grade(digest: Path, label: str) -> dict:
    found = run(
        [sys.executable, str(HERE / "node_to_found.py"), str(digest)],
        cwd=str(WORKSPACE),
    ).stdout
    found_path = OUT_DIR / f"{label}.found.json"
    found_path.write_text(found)
    proc = run(
        [
            sys.executable,
            str(HERE / "runner.py"),
            str(GOLDEN),
            str(found_path),
            "--label",
            f"compare-{label}",
        ],
        cwd=str(WORKSPACE),
    )
    out = proc.stdout
    rec = re.search(r"recall[:\s]+([0-9.]+)", out)
    prec = re.search(r"precision[:\s]+([0-9.]+)", out)
    return {
        "node_recall": float(rec.group(1)) if rec else None,
        "node_precision": float(prec.group(1)) if prec else None,
        "stdout": out,
    }


def claim_grade(digest: Path) -> float | None:
    proc = run(
        [sys.executable, str(HERE / "grade_claim_recall.py"), str(GT), str(digest)],
        cwd=str(WORKSPACE),
        timeout=600,
    )
    m = re.search(r"claim recall:\s*\d+/\d+\s*=\s*([0-9.]+)", proc.stdout)
    if not m:
        print(f"  CLAIM GRADE no parse:\n{proc.stdout[-400:]}\n{proc.stderr[-400:]}")
        return None
    return float(m.group(1))


def claim_count(digest: Path) -> int:
    d = yaml.safe_load(digest.read_text())
    return len(d.get("domain_claims") or []) + len(d.get("infrastructure_claims") or [])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in MODELS:
        print(f"\n=== {model} ===")
        out = OUT_DIR / f"navy-{model}.yaml"
        secs = extract(model, out)
        if secs < 0:
            rows.append({"model": model, "error": "extract failed"})
            continue
        print(f"  extracted in {secs:.0f}s")
        ng = node_grade(out, model)
        cc = claim_count(out)
        cr = claim_grade(out)
        row = {
            "model": model,
            "wall_seconds": round(secs),
            "node_recall": ng["node_recall"],
            "node_precision": ng["node_precision"],
            "claims": cc,
            "claim_recall": cr,
        }
        rows.append(row)
        print(f"  {row}")

    report = OUT_DIR / "report.json"
    report.write_text(json.dumps(rows, indent=2))

    print(
        "\n\n================ MODEL COMPARISON (navy record, same prompt) ============"
    )
    print(
        f"{'model':8} {'wall_s':>7} {'node_rec':>9} {'node_prec':>10} {'claims':>7} {'claim_rec':>10}"
    )
    for r in rows:
        if r.get("error"):
            print(f"{r['model']:8} {'ERROR: ' + r['error']}")
            continue
        print(
            f"{r['model']:8} {r['wall_seconds']:>7} "
            f"{_fmt(r['node_recall']):>9} {_fmt(r['node_precision']):>10} "
            f"{r['claims']:>7} {_fmt(r['claim_recall']):>10}"
        )
    print(f"\nreport: {report}")
    return 0


def _fmt(x) -> str:
    return f"{x:.2f}" if isinstance(x, (int, float)) else "n/a"


if __name__ == "__main__":
    sys.exit(main())
