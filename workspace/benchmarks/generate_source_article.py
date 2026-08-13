#!/usr/bin/env python3
"""Generate a per-SOURCE narrative article by REUSING the assembler's article
contract (prompt, claim formatting, validation), fed all of a source's claims
instead of a single node's. Transport is the API (anomalica key), streaming.
Usage: generate_source_article.py <digest.yaml> <out.json>"""

import json
import sys
from pathlib import Path

import anthropic
import yaml

sys.path.insert(0, "/home/mark/repos/anomalica/assembler")
import assembler  # noqa: E402

digest = yaml.safe_load(open(sys.argv[1]))
out_path = Path(sys.argv[2])

rec = digest.get("record", {})
title = rec.get("title", "Untitled source")
term = digest.get("terminology", {})
main_subject = term.get("main_subject") or title
claims_raw = digest.get("domain_claims", []) or []

# map digest claims -> the dict shape the assembler's format_claim expects
claims = []
ref_counts = {}
for c in claims_raw:
    sp = c.get("speaker")
    sp = sp.get("name") if isinstance(sp, dict) else sp
    for r in c.get("refs") or []:
        nm = r.get("name") if isinstance(r, dict) else r
        ref_counts[nm] = ref_counts.get(nm, 0) + 1
    claims.append(
        {
            "claim_type": c.get("type", "observation"),
            "attestation": c.get("attestation") or "n/a",
            "speaker": sp,
            "date": c.get("date"),
            "content": c.get("text", "") or c.get("content", ""),
            "record_title": title,
            "record_date": rec.get("date"),
            "location": c.get("location"),
        }
    )

# related = the entities, with shared_claims counts (assembler format)
ntype = {n.get("name"): n.get("type") for n in digest.get("nodes", []) or []}
related = []
for nm, cnt in sorted(ref_counts.items(), key=lambda x: -x[1]):
    t = ntype.get(nm)
    if not t:
        continue
    related.append({"name": nm, "type": t, "shared_claims": cnt})

node = {"name": title, "type": "source", "id": rec.get("id", "source")}
prompt = assembler.build_prompt(node, claims, related)

client = anthropic.Anthropic()
with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=8000,
    messages=[{"role": "user", "content": prompt}],
) as stream:
    msg = stream.get_final_message()
response = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

try:
    fm, body = assembler.validate_article(response)
    ok = True
    err = None
except Exception as e:  # noqa: BLE001
    fm, body, ok, err = None, None, False, str(e)

out_path.write_text(
    json.dumps(
        {
            "ok": ok,
            "error": err,
            "title": title,
            "main_subject": main_subject,
            "frontmatter": fm,
            "body": body,
            "raw": response,
            "n_claims": len(claims),
            "n_related": len(related),
        },
        indent=1,
        default=str,
    )
)
print(
    "ok:",
    ok,
    "| body chars:",
    len(body) if body else 0,
    "| refs:",
    len(fm.get("references", [])) if fm else 0,
    "| err:",
    err,
)
