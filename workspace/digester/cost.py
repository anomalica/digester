"""Pre-flight cost estimation for metered extraction runs.

A hard spend gate (anomalica/CLAUDE.md operating rule): no metered API run may
start without printing a dollar estimate and an explicit confirmation. This
module produces the estimate; the CLI enforces the --confirm gate.

The estimate is deliberately rough but conservative - its job is to put a
credible dollar figure in front of a human before any spend, not to be exact.
"""

from __future__ import annotations

# USD per million tokens (input, output). Standard tier, verified 2026-06.
MODEL_PRICING = {
    "haiku": (1.0, 5.0),
    "sonnet": (3.0, 15.0),
    "opus": (5.0, 25.0),
    # API ids, in case the full id is passed
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
}

# Two-pass extraction overhead, calibrated against observed runs:
#   - the static node+claims prompts (~10k tokens) are re-sent on every call;
#   - the document is re-sent across chunks x iterations x two passes;
#   - output is the claims/nodes JSON.
# These multipliers reproduce the navy record (~$0.10 Haiku) and the 180-record
# corpus (~$30-60 Haiku) within the stated +/-40% band.
_PROMPT_OVERHEAD_TOKENS = 10_000
_INPUT_MULTIPLIER = 4.0  # passes x iterations x chunk re-sends
_OUTPUT_RATIO = 1.5  # output tokens per source token
_CHARS_PER_TOKEN = 4.0
_BAND = 0.4  # report a +/-40% range around the point estimate


def estimate_record(source_chars: int, model: str) -> dict:
    """Estimate the cost of extracting one record of `source_chars`."""
    price_in, price_out = MODEL_PRICING.get(model, MODEL_PRICING["sonnet"])
    src_tok = source_chars / _CHARS_PER_TOKEN
    in_tok = (src_tok + _PROMPT_OVERHEAD_TOKENS) * _INPUT_MULTIPLIER
    out_tok = src_tok * _OUTPUT_RATIO
    usd = in_tok / 1e6 * price_in + out_tok / 1e6 * price_out
    return {
        "source_chars": source_chars,
        "est_input_tokens": int(in_tok),
        "est_output_tokens": int(out_tok),
        "usd": usd,
        "usd_low": usd * (1 - _BAND),
        "usd_high": usd * (1 + _BAND),
    }


def estimate_batch(source_chars_list: list[int], model: str) -> dict:
    """Aggregate estimate for a batch of records."""
    per = [estimate_record(c, model) for c in source_chars_list]
    total = sum(p["usd"] for p in per)
    return {
        "records": len(per),
        "total_source_chars": sum(source_chars_list),
        "est_input_tokens": sum(p["est_input_tokens"] for p in per),
        "est_output_tokens": sum(p["est_output_tokens"] for p in per),
        "usd": total,
        "usd_low": total * (1 - _BAND),
        "usd_high": total * (1 + _BAND),
    }


def format_estimate(est: dict, model: str) -> str:
    """Human-readable cost line for the pre-flight gate."""
    price_in, price_out = MODEL_PRICING.get(model, MODEL_PRICING["sonnet"])
    n = est.get("records")
    head = (
        f"{n} record(s), " if n is not None else ""
    ) + f"{est.get('total_source_chars', est.get('source_chars', 0)):,} source chars"
    return (
        "COST ESTIMATE (rough, +/-40%):\n"
        f"  {head}\n"
        f"  model: {model}  (${price_in:.0f}/${price_out:.0f} per million input/output tokens)\n"
        f"  est tokens: ~{est['est_input_tokens']:,} in + ~{est['est_output_tokens']:,} out\n"
        f"  est cost: ~${est['usd']:.2f}  (range ${est['usd_low']:.2f} - ${est['usd_high']:.2f})\n"
        "  This spends real money on the metered Anthropic API."
    )
