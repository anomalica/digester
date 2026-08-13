#!/usr/bin/env python3
"""MVP relevance grader: run models on the segment, grade each against the
highlighted relevant spans by quote overlap. Proves the tuning-mode mechanism
before the workbench annotation UI exists. Ground truth is Fable-drafted
highlights (pending Mark's confirmation). Metered (OpenRouter, ~cents/model)."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
RECORD = HERE / "segment-1-record.md"
HL = HERE / "highlights-segment-1.json"
RUNS = HERE / "runs"

MODELS = [
    "deepseek/deepseek-v4-flash",
    "openai/gpt-5-nano",
    "anthropic/claude-haiku-4.5",
    "qwen/qwen3-235b-a22b-2507",
]
THRESH = 0.4  # token-Jaccard for a quote to "match" a highlight span


def toks(s: str) -> set:
    return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def jac(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def key():
    return subprocess.run(
        [
            str(Path.home() / ".nix-profile/bin/sops"),
            "-d",
            "--extract",
            '["OPENROUTER_API_KEY"]',
            "store/anomalica.yaml",
        ],
        cwd=str(Path.home() / "repos/secrets"),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "SOPS_AGE_KEY_FILE": str(Path.home() / ".config/sops/age/keys.txt"),
        },
    ).stdout.strip()


def run_model(model: str, env: dict) -> Path:
    out = RUNS / f"{re.sub(r'[/:]', '_', model)}.yaml"
    if out.exists():
        return out
    subprocess.run(
        [
            sys.executable,
            "-m",
            "digester.cli",
            "extract",
            str(RECORD),
            "--model",
            model,
            "--confirm",
            "-o",
            str(out),
        ],
        cwd=str(WORKSPACE),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return out


def grade(claim_quotes: list, hl_spans: list) -> dict:
    ct = [toks(q) for q in claim_quotes]
    ht = [toks(s["text"]) for s in hl_spans]
    covered = sum(1 for h in ht if any(jac(h, c) >= THRESH for c in ct))
    on_target = sum(1 for c in ct if any(jac(c, h) >= THRESH for h in ht))
    recall = covered / len(ht) if ht else 0.0
    prec = on_target / len(ct) if ct else 0.0
    f1 = 2 * recall * prec / (recall + prec) if (recall + prec) else 0.0
    missed = [
        s["text"]
        for h, s in zip(ht, hl_spans)
        if not any(jac(h, c) >= THRESH for c in ct)
    ]
    off = [
        q for c, q in zip(ct, claim_quotes) if not any(jac(c, h) >= THRESH for h in ht)
    ]
    return {
        "claims": len(ct),
        "recall": recall,
        "precision": prec,
        "f1": f1,
        "missed": missed,
        "off_target": off,
    }


def main() -> int:
    import yaml

    RUNS.mkdir(exist_ok=True)
    hl = json.load(open(HL))["spans"]
    env = {**os.environ, "DIGESTER_USE_API": "1", "OPENROUTER_API_KEY": key()}

    rows = []
    for m in MODELS:
        out = run_model(m, env)
        if not out.exists():
            rows.append({"model": m, "error": True})
            continue
        d = yaml.safe_load(out.read_text())
        quotes = [c.get("quote", "") for c in (d.get("domain_claims") or [])]
        g = grade(quotes, hl)
        g["model"] = m
        rows.append(g)

    print(
        f"\nGround truth: {len(hl)} highlighted relevant spans (Fable-draft, segment 1)\n"
    )
    print(f"{'model':38} {'claims':>6} {'recall':>7} {'prec':>6} {'f1':>6}")
    print("-" * 66)
    for r in rows:
        if r.get("error"):
            print(f"{r['model']:38} ERROR")
            continue
        print(
            f"{r['model']:38} {r['claims']:>6} {r['recall']:>7.2f} "
            f"{r['precision']:>6.2f} {r['f1']:>6.2f}"
        )
    print()
    for r in rows:
        if r.get("error"):
            continue
        if r["missed"]:
            print(f"[{r['model']}] MISSED {len(r['missed'])} highlight(s):")
            for t in r["missed"]:
                print(f"    - {t[:90]}")
        if r["off_target"]:
            print(
                f"[{r['model']}] OFF-TARGET {len(r['off_target'])} claim(s) (not in any highlight):"
            )
            for t in r["off_target"][:8]:
                print(f"    - {t[:90]}")
    json.dump(
        rows, open(HERE / "grade-segment-1.json", "w"), indent=2, ensure_ascii=False
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
