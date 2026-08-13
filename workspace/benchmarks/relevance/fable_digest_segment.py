#!/usr/bin/env python3
"""One-off: get Fable to digest a transcript segment AND articulate what it
treated as relevant vs skipped, so we can turn its judgement into an explicit
relevance spec for the digester prompt. Metered (OpenRouter, ~$0.10)."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anomalica_common.llm.transport as transport  # noqa: E402
from anomalica_common.llm import _call  # noqa: E402

SEG = Path(__file__).resolve().parent / "segment-1.txt"
MODEL = "anthropic/claude-fable-5"

BRIEF = """You are helping define an extraction standard for Anomalica.

ANOMALICA: a cross-source knowledge graph about UAP (Unidentified Aerial
Phenomena). Many source documents are digested independently; a later
"assimilator" GROUPS the entities and claims from every digest into one graph.
So a single digest is one fragment - its job is faithful, substantive
extraction, NOT deciding global importance (the assimilator does that).

Extract two things:
- NODES (entities), typed as one of: person, organisation, project, place,
  event, object, document, topic. Be COMPLETE: capture every named entity, even
  ones that look tangential - another source may build them up. People are
  "Last, First"; expand acronyms as "Full Name (ACRONYM)".
- CLAIMS: atomic factual assertions, each one standalone fact tied to its source
  quote and speaker. Be STRICT: only claims that carry real, specific
  information worth putting in a knowledge graph.

NOT a valuable claim (skip): a trivial snippet inflated into a sentence; rhetoric
or conversational filler; narration about the interview itself; anything
asserting more specificity than the source states.
VALUABLE: specific facts about UAP and the people/orgs/programmes/events/evidence
around it - who did what, when, where; findings; capabilities; figures; dates;
official actions.

Return a JSON object with keys:
  "nodes": [{"type": ..., "name": ...}],
  "claims": [{"quote": <exact source span>, "text": <the atomic claim>}],
  "relevance_notes": <prose: what you KEPT vs SKIPPED in this segment and why -
     be specific, cite examples - so we can write an explicit relevance spec>
"""


def main() -> int:
    transport.authorise_metered_spend()
    import os

    if not os.environ.get("OPENROUTER_API_KEY"):
        key = subprocess.run(
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
        os.environ["OPENROUTER_API_KEY"] = key

    transport.reset_usage()
    raw = _call(BRIEF, SEG.read_text(), MODEL, schema={"type": "object"})
    usage = transport.get_usage()
    obj = json.loads(raw)

    out = Path(__file__).resolve().parent / "fable-segment-1.json"
    out.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    print(f"=== FABLE digest of segment 1 ===  (cost ${usage['cost_equiv_usd']:.4f})")
    print(f"\nNODES ({len(obj.get('nodes', []))}):")
    for n in obj.get("nodes", []):
        print(f"  ({n.get('type')}) {n.get('name')}")
    print(f"\nCLAIMS ({len(obj.get('claims', []))}):")
    for c in obj.get("claims", []):
        print(f"  - {c.get('text')}")
        print(f"      quote: {c.get('quote')}")
    print("\nRELEVANCE NOTES:\n" + obj.get("relevance_notes", ""))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
