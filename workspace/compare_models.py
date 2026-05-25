"""Side-by-side single-call extraction comparison across haiku/sonnet/opus.

Runs the standard extraction prompt + JSON schema against one chunk of text
using each model in turn. Captures wall time, token usage, cost (as reported
by Claude Code itself), and the structured output. Prints a comparison table
plus sample claims from each.

Intended for ad-hoc evaluation only. Not a CLI command - run via:

    docker run ... python compare_models.py <chapter-file>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from digester.extract import EXTRACTION_PROMPT, DOMAIN_SCHEMA


MODELS = ["haiku", "sonnet", "opus"]


def run_single_call(model: str, chapter_text: str) -> dict:
    """Run one extraction call. Returns metrics + structured output."""
    fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="cmp-")
    with os.fdopen(fd, "w") as f:
        f.write(chapter_text)
    try:
        full_prompt = (
            f"{EXTRACTION_PROMPT}\n\nRead and analyse the document at: {temp_path}"
        )
        cmd = [
            "claude",
            "-p",
            full_prompt,
            "--model",
            model,
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
            "--tools",
            "Read",
            "--effort",
            "low",
            "--json-schema",
            json.dumps(DOMAIN_SCHEMA),
            "--output-format",
            "json",
        ]
        start = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        elapsed = time.time() - start
        if proc.returncode != 0:
            return {
                "model": model,
                "elapsed_s": elapsed,
                "error": proc.stderr[:400] or "non-zero exit, no stderr",
            }
        wrapper = json.loads(proc.stdout)
        structured = wrapper.get("structured_output") or {}
        usage = wrapper.get("usage") or {}
        model_usage = wrapper.get("modelUsage") or {}
        return {
            "model": model,
            "elapsed_s": elapsed,
            "input_tokens": usage.get("input_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cost_usd": wrapper.get("total_cost_usd", 0.0),
            "model_usage": model_usage,
            "node_count": len(structured.get("nodes", [])),
            "claim_count": len(structured.get("claims", [])),
            "nodes": structured.get("nodes", []),
            "claims": structured.get("claims", []),
        }
    finally:
        os.unlink(temp_path)


def print_summary(results: list[dict]) -> None:
    print()
    print("=" * 90)
    print(
        f"{'MODEL':<10} {'TIME':>8} {'INPUT':>10} {'OUTPUT':>10} {'COST':>10} {'NODES':>8} {'CLAIMS':>8}"
    )
    print("=" * 90)
    for r in results:
        if "error" in r:
            print(f"{r['model']:<10} ERROR: {r['error'][:60]}")
            continue
        print(
            f"{r['model']:<10} {r['elapsed_s']:>7.1f}s "
            f"{r['input_tokens']:>10,} {r['output_tokens']:>10,} "
            f"${r['cost_usd']:>9.4f} "
            f"{r['node_count']:>8} {r['claim_count']:>8}"
        )


def print_sample_claims(results: list[dict], n: int = 4) -> None:
    for r in results:
        print()
        print(f"--- {r['model'].upper()} sample claims (first {n}) ---")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        for c in r["claims"][:n]:
            ct = c.get("claim_type", "?")
            att = c.get("attestation", "?")
            content = c.get("content", "")
            excerpt = c.get("original_excerpt") or ""
            refs = c.get("node_references") or []
            print(f"  [{ct}/{att}] {content}")
            if excerpt:
                print(f"      > {excerpt[:160]}")
            if refs:
                print(f"      refs: {', '.join(refs[:5])}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: compare_models.py <chapter-file>", file=sys.stderr)
        sys.exit(1)
    chapter_path = Path(sys.argv[1])
    chapter_text = chapter_path.read_text()
    print(f"Input: {chapter_path} ({len(chapter_text):,} chars)")
    print()

    results: list[dict] = []
    for model in MODELS:
        print(f"Running {model}...", flush=True)
        r = run_single_call(model, chapter_text)
        if "error" in r:
            print(f"  {model} FAILED: {r['error'][:200]}")
        else:
            print(
                f"  {model}: {r['elapsed_s']:.1f}s, "
                f"{r['claim_count']} claims, "
                f"{r['input_tokens']:,} in / {r['output_tokens']:,} out, "
                f"${r['cost_usd']:.4f}"
            )
        results.append(r)

    print_summary(results)
    print_sample_claims(results, n=4)

    # Save full outputs for further inspection
    out_dir = chapter_path.parent / f"{chapter_path.stem}.compare"
    out_dir.mkdir(exist_ok=True)
    for r in results:
        (out_dir / f"{r['model']}.json").write_text(json.dumps(r, indent=2))
    print()
    print(f"Full outputs saved under {out_dir}")


if __name__ == "__main__":
    main()
