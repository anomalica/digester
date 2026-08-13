#!/usr/bin/env python3
"""Combine the assembler's record-level article with the facts/entities breakdown
into the contract-shaped /records/ page (the spec site builds against and the
assembler should emit). Usage: build_record_page.py <article.json> <digest.yaml> <record.md> <out.en.md>"""

import json
import re
import sys
from pathlib import Path

import yaml

art = json.load(open(sys.argv[1]))
digest = yaml.safe_load(open(sys.argv[2]))
record_md = Path(sys.argv[3]).read_text()
out_path = Path(sys.argv[4])

# record metadata from the ingest frontmatter
rec_fm = yaml.safe_load(record_md.split("---", 2)[1])
content_hash = re.sub(r"^sha256:", "", str(rec_fm.get("content_hash", "")))
public_hash = content_hash[:56]
WB_ORIGIN = "http://localhost:5173"  # production: read ANOMALICA_WORKBENCH_ORIGIN
dur = rec_fm.get("duration")
metadata = {
    "medium": rec_fm.get("source_type"),
    "date": str(rec_fm.get("date_published", ""))[:10],
    "publisher": rec_fm.get("publisher"),
}
if dur:
    metadata["duration"] = f"{int(dur) // 60}:{int(dur) % 60:02d}"

nodes = digest.get("nodes", []) or []
ntype = {n.get("name"): n.get("type") for n in nodes}

# entities grouped by type. url = encyclopaedia slug path when a page exists;
# null here (this standalone digest is not in the live graph) -> site renders text.
TYPES = [
    "person",
    "place",
    "event",
    "organisation",
    "project",
    "object",
    "topic",
    "document",
]
entities = {}
for t in TYPES:
    items = [{"name": n["name"], "url": None} for n in nodes if n.get("type") == t]
    if items:
        entities[t] = items

# facts: one self-contained card per domain claim
facts = []
for c in digest.get("domain_claims", []) or []:
    sp = c.get("speaker")
    sp = sp.get("name") if isinstance(sp, dict) else sp
    refs = []
    for r in c.get("refs") or []:
        nm = r.get("name") if isinstance(r, dict) else r
        refs.append({"name": nm, "type": ntype.get(nm, "topic"), "url": None})
    fact = {"text": c.get("text", "")}
    if sp:
        fact["speaker"] = sp
    if c.get("attestation"):
        fact["attestation"] = c["attestation"]
    fact["type"] = c.get("type", "observation")
    if refs:
        fact["refs"] = refs
    if c.get("quote"):
        fact["quote"] = c["quote"]
    if c.get("location"):
        fact["location"] = c["location"]
    if c.get("id"):
        fact["workbench_url"] = f"{WB_ORIGIN}/{public_hash}#claim-{c['id']}"
    facts.append(fact)

afm = art.get("frontmatter") or {}
frontmatter = {
    "title": afm.get("title", art.get("title")),
    "description": afm.get("description", ""),
    "noindex": True,
    "metadata": {k: v for k, v in metadata.items() if v},
    "references": afm.get("references", []),
    "entities": entities,
    "facts": facts,
}
fm_text = yaml.safe_dump(
    frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False
).strip()
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(f"---\n{fm_text}\n---\n\n{art.get('body', '')}\n")
print("wrote", out_path, out_path.stat().st_size, "bytes")
print(
    f"  article: {len(afm.get('references', []))} references, body {len(art.get('body', ''))} chars"
)
print(
    f"  breakdown: {len(facts)} facts, entities {{{', '.join(f'{t}:{len(v)}' for t, v in entities.items())}}}"
)
print(f"  sample workbench_url: {facts[0].get('workbench_url')}")
