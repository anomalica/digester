"""Extraction markdown format: serialise and parse extraction results.

The extraction markdown is the source of truth for the knowledge graph.
The database is derived from these files and can be rebuilt from scratch
at any time.

Format:
    ---
    record_title: ...
    record_id: <uuid>
    record_date: ...
    record_reference: ...
    record_producer: ...
    extracted_at: ...
    model: ...
    ---

    ## Nodes

    ### <uuid> person: David Fravor
    metadata: {"rank": "Commander"}

    ### <uuid> organisation: VFA-41

    ## Domain Claims

    ### <uuid> [observation/first_hand] speaker:David Fravor
    Fravor observed a white oblong object approximately 12 metres in length.
    > Fravor observed a white, oblong object approximately 40 feet long
    refs: David Fravor, Tic Tac UAP
    location: page 2, paragraph 1
    date: 2004-11-14

    ## Infrastructure Claims

    ### <uuid> [opinion/first_hand] speaker:Brown
    ...
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import yaml

from digester.models import (
    ExtractionResult,
    ExtractedClaim,
)


def _write_claims_section(
    lines: list[str], claims: list[ExtractedClaim], section: str
) -> None:
    """Write a claims section to the markdown lines."""
    lines.append(f"## {section}")
    lines.append("")
    for claim in claims:
        claim_id = str(uuid.uuid4())
        speaker_str = f" speaker:{claim.speaker}" if claim.speaker else ""
        lines.append(
            f"### {claim_id} [{claim.claim_type.value}/{claim.attestation.value}]{speaker_str}"
        )
        lines.append(claim.content)
        if claim.original_excerpt:
            lines.append(f"> {claim.original_excerpt}")
        if claim.node_references:
            # Use semicolon delimiter so person names in "Last, First" format
            # round-trip without colliding with the list separator.
            lines.append(f"refs: {'; '.join(claim.node_references)}")
        if claim.location_in_record:
            lines.append(f"location: {claim.location_in_record}")
        if claim.date:
            date_str = claim.date
            if claim.date_end:
                date_str += f" to {claim.date_end}"
            lines.append(f"date: {date_str}")
        lines.append("")


def extraction_to_markdown(
    domain_result: ExtractionResult,
    infra_result: ExtractionResult | None = None,
    record_id: str | None = None,
    model: str = "unknown",
) -> str:
    """Convert extraction results to the extraction markdown format.

    Combines domain and infrastructure results into a single file with
    shared nodes and separate claim sections.
    """
    if record_id is None:
        record_id = str(uuid.uuid4())

    lines = []

    # Frontmatter
    fm = {
        "record_title": domain_result.record_title,
        "record_id": record_id,
        "record_date": domain_result.record_date,
        "record_reference": domain_result.record_reference,
        "record_producer": domain_result.record_producer,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }
    lines.append("---")
    lines.append(yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip())
    lines.append("---")
    lines.append("")

    # Combine and deduplicate nodes from both results
    all_nodes = list(domain_result.nodes)
    seen_names = {n.name for n in all_nodes}
    if infra_result:
        for node in infra_result.nodes:
            if node.name not in seen_names:
                all_nodes.append(node)
                seen_names.add(node.name)

    lines.append("## Nodes")
    lines.append("")
    for node in all_nodes:
        node_id = str(uuid.uuid4())
        line = f"### {node_id} {node.node_type.value}: {node.name}"
        lines.append(line)
        if node.metadata:
            lines.append(f"metadata: {json.dumps(node.metadata)}")
        lines.append("")

    # Domain claims
    _write_claims_section(lines, domain_result.claims, "Domain Claims")

    # Infrastructure claims
    if infra_result and infra_result.claims:
        _write_claims_section(lines, infra_result.claims, "Infrastructure Claims")

    return "\n".join(lines)


# --- Parsing ---

_NODE_PATTERN = re.compile(r"^### ([0-9a-f-]{36}) (\w+): (.+)$")
_CLAIM_PATTERN = re.compile(r"^### ([0-9a-f-]{36}) \[(\w+)/(\w+)\](?: speaker:(.+))?$")


def parse_extraction_markdown(text: str) -> dict:
    """Parse an extraction markdown file into structured data.

    Returns a dict with keys: frontmatter, nodes, domain_claims, infrastructure_claims.
    """
    lines = text.split("\n")

    # Parse frontmatter
    frontmatter = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end:
            try:
                frontmatter = yaml.safe_load("\n".join(lines[1:end])) or {}
            except yaml.YAMLError:
                pass
            body_start = end + 1

    nodes = []
    domain_claims = []
    infrastructure_claims = []
    current_section = None
    current_claim = None
    current_node = None

    def _flush_claim():
        nonlocal current_claim
        if current_claim:
            if current_section == "infrastructure":
                infrastructure_claims.append(current_claim)
            else:
                domain_claims.append(current_claim)
            current_claim = None

    def _flush_node():
        nonlocal current_node
        if current_node:
            nodes.append(current_node)
            current_node = None

    for line in lines[body_start:]:
        stripped = line.strip()

        # Section headers
        if stripped == "## Nodes":
            _flush_claim()
            _flush_node()
            current_section = "nodes"
            continue
        if stripped == "## Domain Claims":
            _flush_claim()
            _flush_node()
            current_section = "domain"
            continue
        if stripped == "## Infrastructure Claims":
            _flush_claim()
            _flush_node()
            current_section = "infrastructure"
            continue

        # Node definition
        if current_section == "nodes":
            m = _NODE_PATTERN.match(stripped)
            if m:
                _flush_node()
                current_node = {
                    "id": m.group(1),
                    "node_type": m.group(2),
                    "name": m.group(3),
                    "metadata": None,
                }
                continue
            if current_node and stripped.startswith("metadata: "):
                try:
                    current_node["metadata"] = json.loads(stripped[10:])
                except json.JSONDecodeError:
                    pass
                continue

        # Claim definition
        if current_section in ("domain", "infrastructure"):
            m = _CLAIM_PATTERN.match(stripped)
            if m:
                _flush_claim()
                current_claim = {
                    "id": m.group(1),
                    "claim_type": m.group(2),
                    "attestation": m.group(3),
                    "speaker": m.group(4),
                    "content": None,
                    "original_excerpt": None,
                    "node_references": [],
                    "location_in_record": None,
                    "date": None,
                    "date_end": None,
                }
                continue

            if current_claim:
                if stripped.startswith("> "):
                    current_claim["original_excerpt"] = stripped[2:]
                elif stripped.startswith("refs: "):
                    raw = stripped[6:]
                    # Semicolon is the current delimiter (so names containing
                    # commas - e.g. "Fravor, David" - round-trip cleanly).
                    # Fall back to comma for legacy files.
                    delim = ";" if ";" in raw else ","
                    current_claim["node_references"] = [
                        r.strip() for r in raw.split(delim) if r.strip()
                    ]
                elif stripped.startswith("location: "):
                    current_claim["location_in_record"] = stripped[10:]
                elif stripped.startswith("date: "):
                    date_str = stripped[6:]
                    if " to " in date_str:
                        parts = date_str.split(" to ", 1)
                        current_claim["date"] = parts[0].strip()
                        current_claim["date_end"] = parts[1].strip()
                    else:
                        current_claim["date"] = date_str
                elif stripped and current_claim["content"] is None:
                    current_claim["content"] = stripped
                continue

    _flush_claim()
    _flush_node()

    return {
        "frontmatter": frontmatter,
        "nodes": nodes,
        "domain_claims": domain_claims,
        "infrastructure_claims": infrastructure_claims,
    }
