"""Extraction pipeline: record text to structured claims and nodes.

Supports two backends:
- Claude CLI (development, via subprocess)
- Anthropic API (production, via anthropic library)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from digester.models import (
    AttestationLevel,
    ClaimType,
    ExtractionResult,
    ExtractedClaim,
    ExtractedNode,
    NodeType,
)

EXTRACTION_PROMPT = """You are extracting structured knowledge from a document for a knowledge graph.

The knowledge graph uses these node types:
- "person": a named human individual
- "organisation": a named group (government bodies, military units, companies, programmes)
- "place": a named geographic location
- "event": a discrete thing that happened at a specific time (must have a date)
- "matter": an ongoing situation spanning a period of time
- "object": a specific named physical thing (craft, materials, devices, samples)

And these claim types:
- "observation": the speaker directly perceived something
- "testimony": formally stated on record or under oath
- "hearsay": relaying what someone else said
- "opinion": expressing a belief or interpretation
- "measurement": instrument or sensor data
- "administrative": dates, funding, personnel assignments, organisational facts

Attestation levels:
- "first_hand": speaker directly observed or participated
- "second_hand": speaker reporting what someone else observed
- "third_hand": speaker reporting what someone heard from someone else

TASK: Extract all nodes and claims from the document below.

RULES:
1. Claims must be atomic - one assertion per claim. Split compound statements.
2. Every claim needs a claim_type and attestation level.
3. node_references in claims should list the names of nodes the claim mentions.
4. Use canonical short names for nodes (e.g. "David Fravor" not "Commander David Fravor, US Navy (Ret.)").
5. Events MUST have a date. If you cannot determine at least a year, use "matter" instead.
6. Normalise all text to English regardless of source language.
7. speaker is the person making the assertion (may differ from the document's author).
8. location_in_record is where in the document the claim appears (page, timestamp, paragraph).
9. UNIT NORMALISATION: Convert all measurements in "content" to metric units using full unit names. Write "10 metres" not "10m", "24,000 metres" not "24km", "1,200 kilometres per hour" not "1200 km/h". Use the exact format: number + space + full unit name (metres, kilometres, kilograms, degrees Celsius, etc.). IMPORTANT: Preserve the original level of precision. If the source says "about 80,000 feet", convert to "approximately 24,000 metres" (rounded), NOT "24,384 metres" (over-precise). Round to the same number of significant figures as the original.
10. original_excerpt: Preserve the EXACT original wording from the source document, including original units, language, and phrasing. This is for attribution and provenance. If the source says "about 30 to 40 feet", the original_excerpt must say exactly that.

OUTPUT FORMAT (respond with ONLY valid JSON, no markdown fencing):

{{"record_title": "short title for this document",
"record_date": "YYYY-MM-DD or YYYY-MM or YYYY if known",
"record_producer": "person or organisation that produced this document",
"nodes": [
    {{"name": "canonical short name", "node_type": "person|organisation|place|event|matter|object", "metadata": {{"date_start": "...", "date_end": "..."}}}}
],
"claims": [
    {{"content": "normalised assertion with metric units",
      "original_excerpt": "exact original wording from the source document",
      "claim_type": "observation|testimony|hearsay|opinion|measurement|administrative",
      "attestation": "first_hand|second_hand|third_hand",
      "speaker": "person name or null",
      "location_in_record": "page 3, paragraph 2",
      "date": "YYYY-MM-DD if applicable",
      "node_references": ["Node A", "Node B"],
      "confidence": 1.0}}
]}}"""

VALID_NODE_TYPES = {
    t.value for t in NodeType if t not in (NodeType.record, NodeType.claim)
}
VALID_CLAIM_TYPES = {t.value for t in ClaimType}
VALID_ATTESTATION = {t.value for t in AttestationLevel}

DEFAULT_MODEL = "sonnet"


NODE_DIRECTORY_HEADER = """EXISTING NODE DIRECTORY - use these EXACT names when referring to known items.
Do NOT create new nodes for items already listed here. Include them in node_references using the exact name from this list.

CRITICAL: Every claim MUST list ALL nodes it mentions in node_references. If a claim says "Kevin Day tracked objects on the USS Princeton", then node_references must include both "Kevin Day" and "USS Princeton". Missing node_references break the knowledge graph.

{directory}

Now extract from the document below. Use canonical names from the directory above where applicable.

"""


def extract(
    text: str,
    model: str = DEFAULT_MODEL,
    use_api: bool = False,
    existing_nodes: list[tuple[str, str]] | None = None,
) -> ExtractionResult:
    """Extract nodes and claims from record text.

    Args:
        existing_nodes: list of (name, node_type) tuples for the node directory.
    """
    prompt = EXTRACTION_PROMPT
    if existing_nodes:
        directory_lines = [
            f"  - {name} ({node_type})" for name, node_type in existing_nodes
        ]
        prompt = (
            NODE_DIRECTORY_HEADER.format(directory="\n".join(directory_lines)) + prompt
        )

    if use_api:
        raw = _call_api(prompt, text, model)
    else:
        raw = _call_cli(prompt, text, model)
    return _parse_response(raw)


def _call_cli(prompt: str, text: str, model: str) -> str:
    """Call Claude via the CLI subprocess."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
        and not k.startswith("CLAUDE_CODE_")
    }
    fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="digester-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        full_prompt = f"{prompt}\n\nRead and analyse the document at: {temp_path}"
        cmd = [
            "claude",
            "-p",
            full_prompt,
            "--model",
            model,
            "--no-session-persistence",
            "--dangerously-skip-permissions",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {proc.stderr}")
        return proc.stdout.strip()
    finally:
        os.unlink(temp_path)


def _call_api(prompt: str, text: str, model: str) -> str:
    """Call Claude via the Anthropic API."""
    import anthropic

    model_map = {
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-6",
        "haiku": "claude-haiku-4-5-20251001",
    }
    model_id = model_map.get(model, model)

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model_id,
        max_tokens=8192,
        messages=[{"role": "user", "content": f"{prompt}\n\nDOCUMENT:\n{text}"}],
    )
    return message.content[0].text


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("Empty response from Claude")
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in response: {cleaned[:200]}")
    cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON from Claude: {e}\nResponse: {cleaned[:500]}"
        ) from e


def _parse_response(raw: str) -> ExtractionResult:
    data = _parse_json(raw)

    nodes = []
    for n in data.get("nodes", []):
        node_type = n.get("node_type", "")
        if node_type not in VALID_NODE_TYPES:
            continue
        nodes.append(
            ExtractedNode(
                name=str(n.get("name", "")),
                node_type=NodeType(node_type),
                metadata=n.get("metadata"),
            )
        )

    claims = []
    for c in data.get("claims", []):
        claim_type = c.get("claim_type", "administrative")
        if claim_type not in VALID_CLAIM_TYPES:
            claim_type = "administrative"
        attestation = c.get("attestation", "first_hand")
        if attestation not in VALID_ATTESTATION:
            attestation = "first_hand"
        refs = c.get("node_references", [])
        if not isinstance(refs, list):
            refs = []
        original = c.get("original_excerpt")
        if isinstance(original, str):
            original = original.strip() or None
        claims.append(
            ExtractedClaim(
                content=str(c.get("content", "")),
                original_excerpt=original,
                claim_type=ClaimType(claim_type),
                attestation=AttestationLevel(attestation),
                speaker=c.get("speaker"),
                location_in_record=c.get("location_in_record"),
                date=c.get("date"),
                date_end=c.get("date_end"),
                node_references=[str(r) for r in refs if r],
                confidence=float(c.get("confidence", 1.0)),
            )
        )

    return ExtractionResult(
        record_title=str(data.get("record_title", "")),
        record_reference=data.get("record_reference"),
        record_date=data.get("record_date"),
        record_producer=data.get("record_producer"),
        nodes=nodes,
        claims=claims,
    )
