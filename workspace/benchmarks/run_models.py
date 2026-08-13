#!/usr/bin/env python3
"""Run the SAME production two-pass extraction across a wide spread of OpenRouter
models on one benchmark record, one digest file per model. Generation only -
grading is a separate, later step. Every model id is an OpenRouter
"provider/model" id, so the transport routes them all through OpenRouter
(a separate prepaid pool), including Anthropic models for comparison.

METERED: spends real OpenRouter credit. Pre-flight estimate is printed; a hard
ceiling stops the run if cumulative cost runs away. The key is read from the
Safe at runtime (never echoed). Usage:

  python benchmarks/run_models.py                # full spread
  python benchmarks/run_models.py <id> [<id>...] # a subset (e.g. a free model)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
ANOM = WORKSPACE.parent.parent

_NAVY_HASH = "1405206f070621abea9b5131b1512eebf13896ce9dc0ced476f4159c796c7e44"
ARTICLE = "navy-pilots"
OUT_DIR = HERE / ARTICLE / "model-runs"
COST_CEILING_USD = 5.0  # backstop: stop launching if cumulative spend exceeds this
CONCURRENCY = (
    5  # models run in parallel - one slow reasoning model can't block the rest
)

# Broad spread, cheapest-first, all OpenRouter ids. Anthropic via OpenRouter is a
# separate prepaid pool (per Mark, 2026-06-30), so the Claude family is included
# for comparison. :free models run first at zero cost to validate the harness.
MODELS = [
    # cheap
    "deepseek/deepseek-v4-flash",
    "qwen/qwen3-235b-a22b-2507",
    "mistralai/mistral-small-3.2-24b-instruct",
    "openai/gpt-5-nano",
    "google/gemini-3.1-flash-lite",
    "meta-llama/llama-4-maverick",
    "z-ai/glm-4.5-air",
    # mid
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3.5-plus-20260420",
    "qwen/qwen3.6-plus",
    "openai/gpt-5-mini",
    "z-ai/glm-5",
    "z-ai/glm-5.1",
    "z-ai/glm-5.2",
    "google/gemini-3-flash-preview",
    "x-ai/grok-4.20",
    # strong / frontier
    "openai/gpt-5.1",
    "openai/gpt-5.2",
    "x-ai/grok-4.3",
    "google/gemini-3.5-flash",
    "google/gemini-3.1-pro-preview",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.8",
]


def resolve_record() -> Path:
    store = ANOM / "ingests/store"
    for c in (
        store / f"{_NAVY_HASH}.v2.md",
        store / f"{_NAVY_HASH}.md",
        store / "v1" / f"{_NAVY_HASH}.v2.md",
        store / "v1" / f"{_NAVY_HASH}.md",
    ):
        if c.exists():
            return c
    raise SystemExit(f"navy benchmark record not found for {_NAVY_HASH}")


def openrouter_key() -> str:
    secrets = Path.home() / "repos/secrets"
    proc = subprocess.run(
        [
            str(Path.home() / ".nix-profile/bin/sops"),
            "-d",
            "--extract",
            '["OPENROUTER_API_KEY"]',
            "store/anomalica.yaml",
        ],
        cwd=str(secrets),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "SOPS_AGE_KEY_FILE": str(Path.home() / ".config/sops/age/keys.txt"),
        },
    )
    if proc.returncode != 0:
        raise SystemExit(f"could not read OPENROUTER_API_KEY: {proc.stderr}")
    return proc.stdout.strip()


def safe(model: str) -> str:
    return re.sub(r"[/:]", "_", model)


def run_one(model: str, record: Path, env: dict) -> dict:
    out = OUT_DIR / f"{ARTICLE}.{safe(model)}.yaml"
    if out.exists():  # resumable: never re-spend on an already-produced digest
        return {"model": model, "out": out.name, "skipped": True, "cost_usd": 0.0}
    t0 = time.time()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "digester.cli",
            "extract",
            str(record),
            "--model",
            model,
            "--confirm",
            "-o",
            str(out),
        ],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        env=env,
        timeout=1800,
    )
    elapsed = time.time() - t0
    # Full stdout holds the per-iteration on_progress lines and the TRACE_JSON
    # line - the loop-by-loop progression source for build_progression.py.
    (OUT_DIR / f"{ARTICLE}.{safe(model)}.stdout.txt").write_text(proc.stdout)
    row: dict = {"model": model, "wall_seconds": round(elapsed, 1), "out": out.name}
    usage = None
    for line in proc.stdout.splitlines():
        if line.startswith("USAGE_JSON:"):
            try:
                usage = json.loads(line.split("USAGE_JSON:", 1)[1])
            except json.JSONDecodeError:
                pass
    if proc.returncode != 0 or not out.exists():
        row["error"] = (proc.stdout[-500:] + "\n" + proc.stderr[-500:]).strip()
        row["cost_usd"] = usage["cost_equiv_usd"] if usage else 0.0
        return row
    row["cost_usd"] = round(usage["cost_equiv_usd"], 6) if usage else 0.0
    row["input_tokens"] = usage["input_tokens"] if usage else None
    row["output_tokens"] = usage["output_tokens"] if usage else None
    try:
        import yaml

        d = yaml.safe_load(out.read_text())
        row["claims"] = len(d.get("domain_claims") or []) + len(
            d.get("infrastructure_claims") or []
        )
        nodes = d.get("nodes")
        if isinstance(nodes, dict):  # type -> [items]
            row["nodes"] = sum(len(v) for v in nodes.values() if isinstance(v, list))
        elif isinstance(nodes, list):  # flat [{id,type,name}, ...]
            row["nodes"] = len(nodes)
        else:
            row["nodes"] = None
    except Exception as e:  # noqa: BLE001
        row["parse_error"] = str(e)
    return row


def _status(row: dict) -> str:
    if row.get("skipped"):
        return "SKIP (exists)"
    if row.get("error"):
        return f"ERROR {str(row['error'])[:120]}"
    return (
        f"{row.get('claims')} claims, {row.get('nodes')} nodes, "
        f"${row.get('cost_usd'):.4f}, {row.get('wall_seconds')}s"
    )


def _safe_run(model: str, record: Path, env: dict) -> dict:
    try:
        return run_one(model, record, env)
    except subprocess.TimeoutExpired:
        return {"model": model, "error": "timeout (1800s)", "cost_usd": 0.0}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": f"driver: {e}", "cost_usd": 0.0}


def main() -> int:
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    record = resolve_record()
    models = sys.argv[1:] or MODELS
    env = {
        **os.environ,
        "DIGESTER_USE_API": "1",
        "OPENROUTER_API_KEY": openrouter_key(),
    }
    manifest = OUT_DIR / "manifest.json"

    rows: list[dict] = []
    pending = []
    for model in models:  # record skips up front; only run what's missing
        out = OUT_DIR / f"{ARTICLE}.{safe(model)}.yaml"
        if out.exists():
            row = {"model": model, "out": out.name, "skipped": True, "cost_usd": 0.0}
            rows.append(row)
            print(f"SKIP {model} (exists)", flush=True)
        else:
            pending.append(model)

    total = 0.0
    done = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(_safe_run, m, record, env): m for m in pending}
        for fut in as_completed(futs):
            row = fut.result()
            rows.append(row)
            done += 1
            total += row.get("cost_usd") or 0.0
            print(
                f"[{done}/{len(pending)}] {row['model']}: {_status(row)}  "
                f"| cumulative ${total:.4f}",
                flush=True,
            )
            manifest.write_text(
                json.dumps(
                    {
                        "record": record.name,
                        "rows": rows,
                        "total_cost_usd": round(total, 4),
                    },
                    indent=2,
                )
            )
            if total > COST_CEILING_USD:
                print(
                    f"\n!! COST CEILING ${COST_CEILING_USD} EXCEEDED - cancelling "
                    f"un-started models",
                    flush=True,
                )
                ex.shutdown(wait=False, cancel_futures=True)
                break

    print(f"\n=== done: {len(rows)} rows, total ${total:.4f} ===")
    print(f"outputs: {OUT_DIR}")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
