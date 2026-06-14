#!/usr/bin/env python3
"""Claim-recall grader: for each must-capture ground-truth fact, decide whether
any extracted claim covers it. Uses Haiku (cheap) with forced-tool structured
output. Usage: grade_claim_recall.py <gt.yaml> <digest.yaml> [model]"""

import sys

import anthropic
import yaml

gt = yaml.safe_load(open(sys.argv[1]))
dig = yaml.safe_load(open(sys.argv[2]))
grader_model = sys.argv[3] if len(sys.argv) > 3 else "claude-haiku-4-5-20251001"

must = [c for c in gt["claims"] if c.get("must")]
claims = [c.get("text", "") for c in dig.get("domain_claims", []) or []]

gt_block = "\n".join(f"G{i}: {c['fact']}" for i, c in enumerate(must))
cl_block = "\n".join(f"C{j}: {t}" for j, t in enumerate(claims))

schema = {
    "type": "object",
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["gt_id", "covered"],
                "properties": {
                    "gt_id": {"type": "string"},
                    "covered": {"type": "boolean"},
                    "claim_id": {
                        "type": "string",
                        "description": "covering claim id like C12, or empty",
                    },
                },
            },
        }
    },
}

prompt = f"""You are grading extraction RECALL. Below is a list of ground-truth FACTS (G0..) that a
good extraction must capture, and a list of extracted CLAIMS (C0..). For EACH ground-truth fact,
decide whether ANY extracted claim conveys that same fact (paraphrase/rewording is fine; the fact
must be substantively present, not merely related). Return one result per ground-truth fact with
covered true/false and the covering claim id if any.

GROUND-TRUTH FACTS:
{gt_block}

EXTRACTED CLAIMS:
{cl_block}
"""

client = anthropic.Anthropic()
with client.messages.stream(
    model=grader_model,
    max_tokens=8000,
    messages=[{"role": "user", "content": prompt}],
    tools=[
        {"name": "emit", "description": "Emit recall results.", "input_schema": schema}
    ],
    tool_choice={"type": "tool", "name": "emit"},
) as stream:
    msg = stream.get_final_message()

res = None
for b in msg.content:
    if getattr(b, "type", None) == "tool_use":
        res = b.input
results = res["results"]
covered = [r for r in results if r.get("covered")]
missed = [r["gt_id"] for r in results if not r.get("covered")]
id_to_fact = {f"G{i}": c["id"] for i, c in enumerate(must)}
print(f"claim recall: {len(covered)}/{len(must)} = {len(covered) / len(must):.2f}")
if missed:
    print("MISSED must-capture:", [id_to_fact.get(m, m) for m in missed])
