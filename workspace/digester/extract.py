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
- "place": a named geographic location. Use "Country, Region, City" or "Country, Feature" format - largest geographic unit first (e.g. "USA, Nevada, Area 51" not "Area 51"; "Australia, Queensland, Tully" not "Tully Queensland"; "Mexico, Gulf of Mexico" not "Gulf of Mexico"). For features that span countries (Persian Gulf, Bermuda Triangle), use the region: "Middle East, Persian Gulf".
- "event": a discrete thing that happened at a specific time (must have a date)
- "matter": an ongoing situation spanning a period of time (programmes, investigations, policy positions)
- "object": a specific named physical thing (craft, materials, devices, samples, sensors, weapons). NOT documents.
- "document": a written or recorded artefact (memo, report, letter, article, paper, book, briefing, video footage, slides, statement, testimony, affidavit, FOIA release). Always use this for textual or recorded artefacts, never "object".

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

TASK: Extract EVERY factual assertion from the document below.

EXHAUSTIVE EXTRACTION: Do not summarise or curate. Capture every factual statement, however incidental - dates, names, places, quoted figures, asides, parenthetical remarks, footnotes, brief observations. A 300-page book contains thousands of claims, not dozens. Coverage matters more than highlighting "important" points.

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
    {{"name": "canonical short name", "node_type": "person|organisation|place|event|matter|object|document", "metadata": {{"date_start": "...", "date_end": "..."}}}}
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


def _extraction_schema(node_types: list[str]) -> dict:
    """Build a JSON schema for an extraction response.

    Passed to `claude --json-schema` so the model cannot return malformed JSON;
    this was the cause of every batch failure in the first-pass run (commas
    dropped mid-generation in long responses).
    """
    return {
        "type": "object",
        "required": ["record_title", "nodes", "claims"],
        "additionalProperties": True,
        "properties": {
            "record_title": {"type": "string"},
            "record_date": {"type": ["string", "null"]},
            "record_producer": {"type": ["string", "null"]},
            "record_reference": {"type": ["string", "null"]},
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "node_type"],
                    "properties": {
                        "name": {"type": "string"},
                        "node_type": {"type": "string", "enum": node_types},
                        "metadata": {"type": ["object", "null"]},
                    },
                },
            },
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["content", "claim_type", "attestation"],
                    "properties": {
                        "content": {"type": "string"},
                        "original_excerpt": {"type": ["string", "null"]},
                        "claim_type": {
                            "type": "string",
                            "enum": sorted(VALID_CLAIM_TYPES),
                        },
                        "attestation": {
                            "type": "string",
                            "enum": sorted(VALID_ATTESTATION),
                        },
                        "speaker": {"type": ["string", "null"]},
                        "location_in_record": {"type": ["string", "null"]},
                        "date": {"type": ["string", "null"]},
                        "date_end": {"type": ["string", "null"]},
                        "node_references": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {"type": "number"},
                    },
                },
            },
        },
    }


DOMAIN_SCHEMA = _extraction_schema(sorted(VALID_NODE_TYPES))
INFRASTRUCTURE_SCHEMA = _extraction_schema(sorted(VALID_NODE_TYPES | {"record"}))

INFRASTRUCTURE_PROMPT = """You are extracting INFRASTRUCTURE information from a document. This is NOT about the phenomena described in the document. It is about the information ecosystem: who produced this content, who interviews whom, what other sources or media are mentioned, career backgrounds, and opinions about other sources.

The knowledge graph uses these node types:
- "person": a named human individual
- "organisation": a named entity distinct from any single person (includes podcasts, news outlets, publications, agencies, companies)
- "place": a named geographic location
- "event": a discrete thing that happened at a specific time (must have a date)
- "matter": an ongoing situation spanning a period of time
- "object": a specific named physical thing
- "record": a specific piece of content (a book, a podcast episode, a documentary, an article)

Claim types:
- "observation": the speaker directly perceived something
- "testimony": formally stated on record or under oath
- "hearsay": relaying what someone else said
- "opinion": expressing a belief or interpretation
- "measurement": instrument or sensor data
- "administrative": dates, career facts, organisational facts

TASK: Extract EVERY infrastructure claim from the document below.

EXHAUSTIVE EXTRACTION: Do not summarise or curate. Capture every infrastructure-related statement, however incidental - every credential mentioned, every show appearance, every cited article, every working relationship, every aside about another journalist. Coverage matters more than highlighting "important" points.

Ignore claims about the phenomena itself. Focus on:

1. INTER-SOURCE REFERENCES: mentions of other media, books, podcasts, documentaries, articles. Include the sentiment (positive, negative, neutral) in metadata.
2. PRODUCTION CONTEXT: who produced this content, who hosts the show, who conducted the interview.
3. CAREER AND BACKGROUND: career history, qualifications, and credentials of speakers and people mentioned, where these are not directly about the phenomena.
4. NETWORK CONNECTIONS: who knows whom, who worked with whom, professional relationships between people in the information ecosystem.
5. OPINIONS ABOUT SOURCES: what speakers think about other media, journalists, organisations in terms of credibility or quality.

Do NOT extract:
- Claims about anomalous phenomena, sightings, encounters, or programmes
- Testimony about what witnesses saw or experienced
- Evidence, sensor data, or investigation findings

OUTPUT FORMAT (respond with ONLY valid JSON, no markdown fencing):

{{"record_title": "short title for this document",
"record_date": "YYYY-MM-DD or YYYY-MM or YYYY if known",
"record_producer": "person or organisation that produced this document",
"nodes": [
    {{"name": "canonical short name", "node_type": "person|organisation|place|event|matter|object|record", "metadata": {{"sentiment": "positive|negative|neutral"}}}}
],
"claims": [
    {{"content": "infrastructure assertion",
      "original_excerpt": "exact original wording from the source document",
      "claim_type": "observation|testimony|hearsay|opinion|measurement|administrative",
      "attestation": "first_hand|second_hand|third_hand",
      "speaker": "person name or null",
      "location_in_record": "page or timestamp",
      "date": "YYYY-MM-DD if applicable",
      "node_references": ["Node A", "Node B"],
      "confidence": 1.0}}
]}}"""

DEFAULT_MODEL = "sonnet"

# Hard upper bound on chunk size. Sonnet's 200K context allows much bigger,
# but very large chunks slow per-call response. 150K chars ~ 37K tokens, well
# inside the window with room for the prompt and growing exclude list.
CHUNK_HARD_MAX = 150_000

# Fall-back char-window settings when there is no chapter structure to use.
CHUNK_MAX_CHARS = 50_000
CHUNK_MIN_CHARS = 20_000

# Iterative extraction caps. After this many rounds, or once a round adds
# fewer than this many new claims, we move on to the next chunk.
ITERATION_MAX = 8
ITERATION_MIN_NEW = 5


def _find_split_point(text: str, lo: int, hi: int) -> int | None:
    """Return the latest natural boundary inside text[lo:hi], or None.

    Preference order: page marker > heading > paragraph break > line break.
    """
    window = text[lo:hi]
    for pattern in (
        "\n<!-- file_page: ",
        "\n## ",
        "\n\n",
        "\n",
    ):
        idx = window.rfind(pattern)
        if idx > 0:
            return lo + idx + 1  # split after the preceding newline
    return None


def _chunk_text(
    text: str,
    max_chars: int = CHUNK_MAX_CHARS,
    min_chars: int = CHUNK_MIN_CHARS,
) -> list[str]:
    """Split text into chunks respecting natural boundaries.

    Each chunk is at most `max_chars` characters. Boundaries are chosen
    backwards from the max so paragraphs and headings are not cut. For text
    shorter than max_chars, returns a single-element list.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    pos = 0
    while pos < len(text):
        end = min(pos + max_chars, len(text))
        if end < len(text):
            split = _find_split_point(text, pos + min_chars, end)
            if split is not None:
                end = split
        chunks.append(text[pos:end])
        pos = end
    return chunks


def _split_at_chapters(text: str) -> list[str] | None:
    """Split on Anomalica record-format chapter markers.

    The canonical chapter boundary in the record-format spec is
    `<!-- chapter: N -->` (primarily on ebooks). Returns None if the document
    has no chapter annotations, in which case the caller falls back to
    char-window chunking. We trust the annotation - no minimum size check.
    """
    import re

    parts = re.split(r"\n(?=<!-- chapter: )", text)
    if len(parts) < 2:
        return None
    return parts


def _build_chunks(text: str) -> list[str]:
    """Top-level chunker.

    1. If the document has `## ` headings sized like real chapters, use them.
    2. If any chapter is bigger than the hard cap, sub-chunk it on char windows.
    3. If no chapter structure, fall back to char-window chunking throughout.
    """
    chapters = _split_at_chapters(text)
    if chapters is None:
        return _chunk_text(text)
    final: list[str] = []
    for ch in chapters:
        if len(ch) > CHUNK_HARD_MAX:
            final.extend(_chunk_text(ch, max_chars=CHUNK_HARD_MAX, min_chars=50_000))
        else:
            final.append(ch)
    return final


def _format_exclude_list(claims: list[ExtractedClaim]) -> str:
    """Compact numbered list of claim content - what the model has already extracted."""
    return "\n".join(f"{i + 1}. {c.content}" for i, c in enumerate(claims))


def _iterate_chunk(
    chunk_text: str,
    base_prompt: str,
    schema: dict,
    model: str,
    use_api: bool,
    directory: list[tuple[str, str]],
    on_progress=None,
) -> tuple[list[ExtractedNode], list[ExtractedClaim], ExtractionResult]:
    """Iteratively extract from a single chunk until the model stops finding more.

    Each round shows the model what has already been extracted and asks for
    additional claims. Claude Code CLI auto-caches identical prompt prefixes
    within the 5-minute TTL, so rounds 2..N cost roughly the new exclude-list
    text plus the output - the chunk body is cached.

    Returns (nodes, claims, first_round_result). The first-round result is
    surfaced so the caller can read record-level metadata (title, date,
    producer) from the very first response.
    """
    chunk_nodes: dict[str, ExtractedNode] = {}
    chunk_claims: list[ExtractedClaim] = []
    seen_content: set[str] = set()
    first_result: ExtractionResult | None = None

    for iteration in range(ITERATION_MAX):
        prompt = base_prompt
        if directory:
            directory_lines = [
                f"  - {name} ({node_type})" for name, node_type in directory
            ]
            prompt = (
                NODE_DIRECTORY_HEADER.format(directory="\n".join(directory_lines))
                + prompt
            )
        if chunk_claims:
            prompt += (
                "\n\nALREADY EXTRACTED CLAIMS - do NOT repeat any of these:\n"
                + _format_exclude_list(chunk_claims)
                + "\n\nExtract ADDITIONAL factual claims from the document that are NOT in the list above. Return an empty `claims` array if you cannot find any genuinely new claims."
            )

        if use_api:
            raw = _call_api(prompt, chunk_text, model)
        else:
            raw = _call_cli(prompt, chunk_text, model, schema=schema)
        result = _parse_response(raw)
        if first_result is None:
            first_result = result

        new_in_round = 0
        for claim in result.claims:
            key = claim.content.strip().lower()
            if key in seen_content:
                continue
            seen_content.add(key)
            chunk_claims.append(claim)
            new_in_round += 1

        for node in result.nodes:
            if node.name not in chunk_nodes:
                chunk_nodes[node.name] = node
                directory.append((node.name, node.node_type.value))

        if on_progress:
            on_progress(
                f"    iter {iteration + 1}: +{new_in_round} claims "
                f"(chunk total {len(chunk_claims)})"
            )

        if new_in_round < ITERATION_MIN_NEW:
            break

    return list(chunk_nodes.values()), chunk_claims, first_result


NODE_DIRECTORY_HEADER = """EXISTING NODE DIRECTORY - use these EXACT names when referring to known items.
Do NOT create new nodes for items already listed here. Include them in node_references using the exact name from this list.

CRITICAL: Every claim MUST list ALL nodes it mentions in node_references. If a claim says "Kevin Day tracked objects on the USS Princeton", then node_references must include both "Kevin Day" and "USS Princeton". Missing node_references break the knowledge graph.

{directory}

Now extract from the document below. Use canonical names from the directory above where applicable.

"""


def _extract_chunked(
    text: str,
    base_prompt: str,
    schema: dict,
    model: str,
    use_api: bool,
    existing_nodes: list[tuple[str, str]] | None,
    on_progress=None,
) -> ExtractionResult:
    """Shared chunked-extraction implementation.

    Splits the record at chapter boundaries when available (falls back to char
    windows). Within each chunk runs an iterative loop - re-asking the model
    for "more claims not in this list" until output dries up. The running node
    directory threads through subsequent chunks so canonical names persist.
    """
    chunks = _build_chunks(text)
    directory: list[tuple[str, str]] = list(existing_nodes or [])
    merged_nodes: list[ExtractedNode] = []
    seen_names: set[str] = set()
    merged_claims: list[ExtractedClaim] = []
    record_title = ""
    record_date = None
    record_producer = None
    record_reference = None

    for idx, chunk in enumerate(chunks):
        if on_progress and len(chunks) > 1:
            on_progress(f"  chunk {idx + 1}/{len(chunks)} ({len(chunk):,} chars)")

        chunk_nodes, chunk_claims, first_round = _iterate_chunk(
            chunk_text=chunk,
            base_prompt=base_prompt,
            schema=schema,
            model=model,
            use_api=use_api,
            directory=directory,
            on_progress=on_progress,
        )

        # Record-level metadata is captured from the very first chunk's first
        # round only - subsequent chunks describe the same record so their
        # title/date/etc may be partial or wrong.
        if idx == 0 and first_round is not None:
            record_title = first_round.record_title
            record_date = first_round.record_date
            record_producer = first_round.record_producer
            record_reference = first_round.record_reference

        for node in chunk_nodes:
            if node.name not in seen_names:
                seen_names.add(node.name)
                merged_nodes.append(node)
        merged_claims.extend(chunk_claims)

    return ExtractionResult(
        record_title=record_title,
        record_reference=record_reference,
        record_date=record_date,
        record_producer=record_producer,
        nodes=merged_nodes,
        claims=merged_claims,
    )


def extract(
    text: str,
    model: str = DEFAULT_MODEL,
    use_api: bool = False,
    existing_nodes: list[tuple[str, str]] | None = None,
    on_progress=None,
) -> ExtractionResult:
    """Extract nodes and claims from record text.

    Args:
        existing_nodes: list of (name, node_type) tuples for the node directory.
        on_progress: optional callback receiving status strings (for chunked runs).
    """
    return _extract_chunked(
        text=text,
        base_prompt=EXTRACTION_PROMPT,
        schema=DOMAIN_SCHEMA,
        model=model,
        use_api=use_api,
        existing_nodes=existing_nodes,
        on_progress=on_progress,
    )


def extract_infrastructure(
    text: str,
    model: str = DEFAULT_MODEL,
    use_api: bool = False,
    existing_nodes: list[tuple[str, str]] | None = None,
    on_progress=None,
) -> ExtractionResult:
    """Extract infrastructure information from record text.

    Focuses on the information ecosystem: inter-source references,
    production context, career backgrounds, network connections.
    """
    return _extract_chunked(
        text=text,
        base_prompt=INFRASTRUCTURE_PROMPT,
        schema=INFRASTRUCTURE_SCHEMA,
        model=model,
        use_api=use_api,
        existing_nodes=existing_nodes,
        on_progress=on_progress,
    )


def _call_cli(prompt: str, text: str, model: str, schema: dict | None = None) -> str:
    """Call Claude via the CLI subprocess.

    Restricts Claude Code to the Read tool with --effort low to avoid the full
    agentic stack (Bash, Edit, MCP servers, skills, auto-memory, etc.) that
    is loaded by default. Without these flags a structured-extraction call
    can take 8+ minutes because the model spends time choosing between tools.
    With them, the same call completes in 10-30 seconds.

    When `schema` is provided, it is passed via --json-schema so the model
    cannot emit malformed JSON (the failure mode that cost us records in the
    first batch run).
    """
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
            "--disable-slash-commands",
            "--tools",
            "Read",
            "--effort",
            "low",
        ]
        if schema is not None:
            # --json-schema makes Claude emit via a StructuredOutput tool call;
            # the validated object lives in `structured_output` of the JSON
            # wrapper, not in the text stream. We unwrap it here and re-encode
            # so downstream _parse_json sees a normal JSON string.
            cmd.extend(["--json-schema", json.dumps(schema)])
            cmd.extend(["--output-format", "json"])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {proc.stderr}")
        if schema is not None:
            try:
                wrapper = json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Claude CLI emitted non-JSON wrapper: {e}\n{proc.stdout[:500]}"
                ) from e
            structured = wrapper.get("structured_output")
            if structured is None:
                raise RuntimeError(
                    f"Claude CLI returned no structured_output. "
                    f"is_error={wrapper.get('is_error')} "
                    f"api_error_status={wrapper.get('api_error_status')} "
                    f"result_preview={str(wrapper.get('result'))[:200]}"
                )
            return json.dumps(structured)
        return proc.stdout.strip()
    finally:
        os.unlink(temp_path)


def _call_api(prompt: str, text: str, model: str) -> str:
    """Call Claude via the Anthropic API."""
    import anthropic

    model_map = {
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-7",
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
    if start == -1:
        raise ValueError(f"No JSON object found in response: {cleaned[:200]}")
    decoder = json.JSONDecoder()
    try:
        obj, _idx = decoder.raw_decode(cleaned[start:])
        return obj
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON from Claude: {e}\nResponse: {cleaned[start : start + 500]}"
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
