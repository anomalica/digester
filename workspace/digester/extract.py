"""Extraction pipeline: record text to structured claims and nodes.

Supports two backends:
- Claude CLI (development, via subprocess)
- Anthropic API (production, via anthropic library)
"""

from __future__ import annotations

import hashlib
import json

from digester import accounts as accounts_mod
from anomalica_common.llm import check_route_capacity
from anomalica_common.pre_digest import materialise
from digester import prompt_registry
import re

from anomalica_common.digest import (
    AttestationLevel,
    ClaimType,
    ExtractionResult,
    ExtractedClaim,
    ExtractedNode,
    NodeType,
    OriginKind,
)
from anomalica_common.llm import (
    call_with_document,
    DEFAULT_MODEL,
    _call,
    _call_api,
    _call_cli,
    _parse_json,
)

VALID_NODE_TYPES = {
    t.value
    for t in NodeType
    if t not in (NodeType.record, NodeType.claim, NodeType.matter)
}
# ORDERED, NOT SETS, and the order is the enum's own declaration order.
#
# These go into the JSON schema the model is constrained by, and a set has no
# order: Python randomises string hashing per process, so every run presented
# the model with `claim_type` and `attestation` options shuffled differently.
# Three consecutive processes produced three different orderings of the same
# six values. Option order biases a model's choice, so this was an uncontrolled
# variable sitting inside every run-to-run comparison we have made - including
# the measured noise floor, and including a schema fingerprint that was
# supposed to prove two digests came from one configuration and was different
# every time it was computed. VALID_ORIGIN_KINDS was already sorted, which is
# the same bug found once and fixed in one of the three places.
VALID_CLAIM_TYPES = tuple(t.value for t in ClaimType)
VALID_ATTESTATION = tuple(t.value for t in AttestationLevel)
VALID_ORIGIN_KINDS = tuple(k.value for k in OriginKind)


SAFE_ACRONYMS = (
    "UFO",  # Unidentified Flying Object - domain-universal
    "UAP",  # Unidentified Anomalous Phenomena - domain-universal
    "CIA",  # Central Intelligence Agency
    "FBI",  # Federal Bureau of Investigation
    "NSA",  # National Security Agency
    "NASA",  # National Aeronautics and Space Administration
    "DOD",  # Department of Defense (also DoD)
    "DoD",
    "FAA",  # Federal Aviation Administration
    "NATO",  # North Atlantic Treaty Organization
    "UN",  # United Nations
    "EU",  # European Union
    "US",  # United States
    "USA",
    "UK",  # United Kingdom
    "USSR",  # Soviet Union (historical)
    "GPS",  # Global Positioning System
    "TV",  # Television
)


def _safe_acronym_prompt_block() -> str:
    """Standard prompt block telling the model not to expand universal acronyms."""
    listed = ", ".join(SAFE_ACRONYMS)
    return (
        f"SAFE ACRONYMS (always use bare, never expand): {listed}. "
        f"These are universally recognisable; expanding them adds noise. "
        f"Do not write 'Unidentified Flying Object (UFO)' - write 'UFO'. "
        f"Do not write 'Central Intelligence Agency (CIA)' - write 'CIA'. "
        f"This applies to claim text AND node names AND the terminology "
        f"pre-pass output - none of these acronyms should appear in the "
        f"document's acronyms map.\n\n"
    )


# ----------------------------------------------------------------------------
# Terminology pre-pass: one Claude call per record that reads the document
# top-to-bottom and returns the document's central matter (and any key event)
# with a canonical portable name, plus a map of codenames and acronyms found
# in the source. The result is injected as context into every claim-extraction
# chunk so the claims anchor to a single canonical name and resolve codenames
# at write time rather than emitting "FASTEAGLE 01" as a node.
# ----------------------------------------------------------------------------

TERMINOLOGY_PROMPT = """You are doing a TERMINOLOGY EXTRACTION pre-pass on a document. You are NOT extracting claims yet. Your only task is to read the document and identify:

1. THE MAIN MATTER - the broad ongoing subject the whole document covers. This is what a knowledgeable reader would say the document is "about" in one sentence. For a Nimitz incident report it is the week-long detection-and-intercept period off the western coast, not a single flight. For a book it is the book itself. For congressional testimony it is the testimony as an event.

2. THE MAIN EVENT (only if applicable) - a single dated occurrence that is the document's principal subject, distinct from the broader matter. For the Nimitz incident report this is the 14 November F/A-18F intercept; for a testimony document there is usually no separate "main event" because the testimony IS the document.

3. CODENAMES - operational shorthand the document uses that resolves to an underlying entity. Callsigns (FASTEAGLE 01, FASTEAGLE 02), military codenames (Tic Tac, Fast Walker), internal nicknames. For each codename, identify what it actually refers to: which person, which aircraft, which object. Use real identifiers in the resolution, not other codenames.

4. ACRONYMS - any abbreviation the document uses for which the full form matters: agency acronyms (AATIP, AARO, DIA), domain shorthand (AAV, FLIR, WSO, NDA, MISREP, CAP, CVIC, SCIF), squadron designators (VFA-41, CSG-11) etc.

   EXCLUDE the universally-recognised SAFE ACRONYMS: UFO, UAP, CIA, FBI, NSA, NASA, DOD, DoD, FAA, NATO, UN, EU, US, USA, UK, USSR, GPS, TV, CPU, GPU, USB, URL, API. These do NOT need expansion - do not include them in the acronyms list you return. Their full forms are universally known.

NAMING RULES for the canonical names you produce - aim for the Wikipedia article-title register: short, memorable, recognisable. The Nimitz UFO incident's Wikipedia article is titled "USS Nimitz UFO incident" - four words and a stranger can look it up. Aim for that.

- MAX 8 WORDS. If your candidate name is longer, you are over-engineering it.
- Include the universally-recognised identifier (Nimitz, AATIP, Elizondo, Fravor, Roswell). NEVER put a codename or callsign (FASTEAGLE, Tic Tac as a codename) in the canonical name. "Tic Tac" can appear if the source uses it as a shape description, but as a codename for the object it is not the canonical identifier.
- Do NOT repeat the node type in the name. The "type" field says matter/event/etc.; the name does not need to end with "Matter" or "Event" or "Detection Matter". "Nimitz UAP Incident" is the name; the type is matter. Not "Nimitz UAP Detection Matter".
- Date: use the SHORTEST ISO form that distinguishes it. Prefer year ("2004"), use year-month ("2004-11") when needed to disambiguate, use full date ("2004-11-14") only for specific dated events. Do NOT pack a full date range into the name - the matter node carries its date range as separate metadata. "Nimitz UAP Incident, 2004" beats "Nimitz Anomalous Aerial Vehicle Incident, 2004-11-10 to 2004-11-16".
- Acronyms: only expand bare ones that are opaque. "UAP" is universal in this domain - leave it. "AAV" is opaque - either expand it ("Anomalous Aerial Vehicle Incident") or use the more universal substitute ("UAP Incident"). Prefer the most universal short form.
- ISO month names only: never "14 November 2004". If you need a date in the name at all, it is "2004-11-14".
- NO "the" in the name - it implies a known referent. "Nimitz UAP Incident, 2004", not "The Nimitz UAP Incident".

GOOD canonical name examples (the register to aim for):
- "Nimitz UAP Incident, 2004" (matter, ~4 words)
- "Nimitz F/A-18F UAP Intercept, 2004-11-14" (event, specific date)
- "Elizondo Senate UAP Testimony, 2024-11" (event)
- "Imminent (Elizondo book, 2024)" (matter)
- "Lex Fridman Podcast 122: Fravor, 2020-09" (event)
- "Coulthart In Plain Sight (2023 book)" (matter)
- "Roswell Crash, 1947" (event)

BAD canonical name examples (do not produce these):
- "Nimitz Carrier Strike Group (CSG-11) Anomalous Aerial Vehicle (AAV) Detection Matter, 2004-11-10 to 2004-11-16" - way too long, repeats node type in name, full date range crammed in
- "FASTEAGLE Flight AAV Intercept 14 November 2004" - codename in name, bare acronym, spelled month
- "AAV Detection Period" - missing identifier, bare acronym
- "Nimitz CSG-11 AAV Detection Matter" - reads like a database key, has "Matter" suffix repeating the node type
- "AAV Detection Matter" - bare acronym, has "Matter" suffix

EMPHATIC: never put "Matter", "Event", "Concept", "Document", "Organisation" etc. as a suffix in a canonical name. The node TYPE is a separate field; the name does not repeat it.

OUTPUT FORMAT - respond with ONLY valid JSON, no markdown fencing:

{{
  "main_matter": {{
    "name": "canonical portable name following the rules above",
    "type": "event or organisation or pattern (use 'event' for a dated subject the document is about; 'organisation' for a named body/programme the document covers; 'pattern' for a cross-case shape the document analyses; 'matter' is no longer a valid type)",
    "date": "YYYY-MM-DD or YYYY-MM or YYYY or YYYY-MM-DD to YYYY-MM-DD"
  }},
  "main_event": {{
    "name": "canonical portable name, or null if no distinct main event",
    "type": "event",
    "date": "YYYY-MM-DD"
  }} or null,
  "codenames": [
    {{"codename": "FASTEAGLE 01", "refers_to": "F/A-18F flown by David Fravor during the 2004-11-14 intercept"}},
    {{"codename": "FASTEAGLE 02", "refers_to": "F/A-18F flown by Alex Dietrich during the 2004-11-14 intercept"}}
  ],
  "acronyms": [
    {{"acronym": "AAV", "expansion": "Anomalous Aerial Vehicle"}},
    {{"acronym": "FLIR", "expansion": "forward-looking infrared"}},
    {{"acronym": "CSG-11", "expansion": "Carrier Strike Group 11"}}
  ]
}}

DOCUMENT TEXT:
"""


TERMINOLOGY_SCHEMA = {
    "type": "object",
    "properties": {
        "main_matter": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["event", "organisation", "pattern"],
                },
                "date": {"type": "string"},
            },
            "required": ["name", "type"],
        },
        "main_event": {
            "type": ["object", "null"],
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": ["event"]},
                "date": {"type": "string"},
            },
        },
        "codenames": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "codename": {"type": "string"},
                    "refers_to": {"type": "string"},
                },
                "required": ["codename", "refers_to"],
            },
        },
        "acronyms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "acronym": {"type": "string"},
                    "expansion": {"type": "string"},
                },
                "required": ["acronym", "expansion"],
            },
        },
    },
    "required": ["main_matter", "codenames", "acronyms"],
}

# Cap how much of the body the terminology pre-pass sees. Most documents fit
# comfortably; books get the first 80K chars which is enough to identify
# main matter, principal codenames, and the recurring acronyms.
TERMINOLOGY_SAMPLE_CHARS = 80_000


def extract_terminology(
    text: str,
    model: str = DEFAULT_MODEL,
    use_api: bool = False,
    record_context: str = "",
    on_progress=None,
) -> dict:
    """Run the terminology pre-pass. Returns a dict matching TERMINOLOGY_SCHEMA.

    On any failure (parse error, malformed response) returns a minimal stub so
    the claim-extraction pipeline can continue. The pre-pass is a help, not a
    hard requirement: missing terminology degrades quality but the pipeline
    still produces output.
    """
    log = on_progress or (lambda _: None)
    sample = text[:TERMINOLOGY_SAMPLE_CHARS]
    if len(text) > TERMINOLOGY_SAMPLE_CHARS:
        sample += "\n\n[document continues - terminology pre-pass sees only the first "
        sample += f"{TERMINOLOGY_SAMPLE_CHARS} characters]"
    log("Running terminology pre-pass...")
    prompt = record_context + TERMINOLOGY_PROMPT
    try:
        if use_api:
            raw = _call_api(prompt, sample, model)
        else:
            raw = _call_cli(prompt, sample, model, schema=TERMINOLOGY_SCHEMA)
        # Strip any wrapping if the CLI returned a structured_output envelope
        parsed = _parse_terminology_response(raw)
    except Exception as exc:  # noqa: BLE001 - keep extraction running on failure
        log(f"  terminology pre-pass FAILED: {exc}")
        return _stub_terminology()

    mm = parsed.get("main_matter") or {}
    mm_name = mm.get("name") or "(unknown main matter)"
    log(f"  main matter: {mm_name}")
    if parsed.get("main_event"):
        log(f"  main event:  {parsed['main_event'].get('name', '')}")
    log(
        f"  {len(parsed.get('codenames') or [])} codenames, "
        f"{len(parsed.get('acronyms') or [])} acronyms"
    )
    return parsed


def _parse_terminology_response(raw: str) -> dict:
    """Pull the first JSON object out of the model's response."""
    raw = raw.strip()
    # Some CLI wrappers return {"structured_output": {...}} or wrap in fences.
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(raw.lstrip())
    if isinstance(obj, dict) and "structured_output" in obj:
        obj = obj["structured_output"]
    return obj


def _stub_terminology() -> dict:
    return {
        "main_matter": {"name": "", "type": "event"},
        "main_event": None,
        "codenames": [],
        "acronyms": [],
    }


def format_terminology_context(terminology: dict) -> str:
    """Format the terminology pre-pass result as a prelude block for the
    claim-extraction prompt. The model is told to use the main matter name
    verbatim as the anchor in every claim, and to resolve codenames at write
    time instead of emitting them as nodes."""
    mm = terminology.get("main_matter") or {}
    mm_name = mm.get("name") or ""
    me = terminology.get("main_event")
    codenames = terminology.get("codenames") or []
    acronyms = terminology.get("acronyms") or []

    lines: list[str] = ["DOCUMENT TERMINOLOGY (resolved before claims):", ""]
    if mm_name:
        mm_type = mm.get("type") or "event"
        lines.append(
            f"THE MAIN {mm_type.upper()} NODE FOR THIS DOCUMENT ALREADY EXISTS: "
            f'name="{mm_name}", type={mm_type}.'
        )
        lines.append(
            "  DO NOT emit a different node for the same subject with a "
            "different name. Use this EXACT name verbatim if you reference "
            "the main subject as a node. Use this EXACT name as the anchor "
            "at the start of every claim that does not uniquely belong to a "
            "specific sub-event. Do not paraphrase, do not shorten, do not "
            "extend with extra descriptors. Copy the string."
        )
    if me and me.get("name"):
        me_type = me.get("type") or "event"
        lines.append("")
        lines.append(
            f"THE MAIN {me_type.upper()} NODE ALREADY EXISTS: "
            f'name="{me["name"]}", type={me_type}.'
        )
        lines.append(
            "  Use this EXACT name as the anchor only for claims uniquely "
            "about this specific dated sub-event. For all other claims use "
            "the main matter name above."
        )
    if codenames:
        lines.append("")
        lines.append(
            "CODENAMES the document uses - these are aliases for real entities. "
            "Do NOT emit a codename as a node. Resolve it to its referent and "
            "mention the codename only in passing inside claim text if useful:"
        )
        for c in codenames:
            lines.append(f'  - "{c.get("codename", "")}" = {c.get("refers_to", "")}')
    if acronyms:
        lines.append("")
        lines.append(
            "ACRONYMS - always write in full with the acronym in parens "
            '"Full Form (ACRONYM)" on every appearance in a node name, and on '
            "the first appearance within each claim text:"
        )
        for a in acronyms:
            lines.append(f"  - {a.get('acronym', '')} = {a.get('expansion', '')}")

    lines.append("")
    return "\n".join(lines) + "\n"


# Hard upper bound on chunk size. Sonnet's 200K context allows much bigger,
# but very large chunks slow per-call response. 150K chars ~ 37K tokens, well
# inside the window with room for the prompt and growing exclude list.
CHUNK_HARD_MAX = 150_000

# Fall-back char-window settings when there is no chapter structure to use.
CHUNK_MAX_CHARS = 50_000
CHUNK_MIN_CHARS = 20_000

# Iterative extraction stopping rule. The loop stops when a round adds fewer
# than ITERATION_MIN_NEW genuinely-new claims (dedup means repeats don't count,
# so this converges on its own). ITERATION_MAX is a runaway safety ceiling
# ONLY - not the normal exit. Previously ITERATION_MAX=8 was the de-facto
# stopping mechanism and was cutting off chunks that still had claims to give.
ITERATION_MAX = 40
ITERATION_MIN_NEW = 2


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
    """Split on Anomalica ingest-format chapter markers.

    The canonical chapter boundary in the ingest-format spec is
    `<!-- chapter: N -->` (primarily on ebooks). Returns None if the document
    has no chapter annotations, in which case the caller falls back to
    char-window chunking. We trust the annotation - no minimum size check.
    """

    parts = re.split(r"\n(?=<!-- chapter: )", text)
    if len(parts) < 2:
        return None
    return parts


def _build_chunks(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Top-level chunker.

    1. If the document has `## ` headings sized like real chapters, use them.
    2. If any chapter is bigger than the hard cap, sub-chunk it on char windows.
    3. If no chapter structure, fall back to char-window chunking throughout.
    """
    chapters = _split_at_chapters(text)
    if chapters is None:
        return _chunk_text(text, max_chars=max_chars)
    final: list[str] = []
    hard_max = min(CHUNK_HARD_MAX, max(max_chars, CHUNK_MIN_CHARS))
    for ch in chapters:
        if len(ch) > hard_max:
            final.extend(
                _chunk_text(ch, max_chars=hard_max, min_chars=min(50_000, hard_max))
            )
        else:
            final.append(ch)
    return final


# The claims pass emits FAR more per chunk than the nodes pass: one object per
# claim, each now carrying a provenance chain (ADR 0044), so a 50k-char chunk asks
# for ~90 claims and tens of thousands of output tokens in a single call. Haiku
# could not finish one inside the 900s CLI timeout and the whole run died after the
# nodes pass had already been paid for. Chunk the claims pass smaller: same total
# work, bounded output per call.
CLAIMS_CHUNK_MAX_CHARS = 20_000


def _claim_key_v2(c: dict) -> tuple[str, str, str, str]:
    """Identity of a claim: the proposition PLUS the provenance it was asserted with.

    Content alone is NOT identity, and the two-pass claims dedup is global across
    chunks, so a content-only key is actively destructive: a proposition teased
    early (bare, unattributed) suppresses the same proposition where the source
    later names who told them and how it reached them. The attributed instance is
    the one worth keeping, so the key must see the provenance.
    """
    return (
        (c.get("content") or "").strip().lower(),
        c.get("claim_type") or "",
        c.get("attestation") or "",
        (c.get("speaker") or "").strip().lower(),
    )


NODE_DIRECTORY_HEADER = """EXISTING NODE DIRECTORY - use these EXACT names when referring to known items.
Do NOT create new nodes for items already listed here. Include them in node_references using the exact name from this list.

CRITICAL: Every claim MUST list ALL nodes it mentions in node_references. If a claim says "Kevin Day tracked objects on the USS Princeton", then node_references must include both "Kevin Day" and "USS Princeton". Missing node_references break the knowledge graph.

{directory}

Now extract from the document below. Use canonical names from the directory above where applicable.

"""


def build_record_context(
    title: str,
    creators: list[str] | None,
    date: str | None,
    source_type: str | None,
) -> str:
    """Build the SOURCE RECORD framing prepended to every extraction prompt.

    Pins first-person / "the author" references to the named creator so the
    model never emits an unpinned "the author" node. Unpinned nodes are
    graph-wide contaminants because nodes are global (shared across records),
    so a vague node would wrongly merge across every first-person source.
    """
    creator_str = ", ".join(creators) if creators else None
    bits = [f'"{title}"' if title else "an untitled record"]
    if source_type:
        bits.append(f"({source_type})")
    if creator_str:
        bits.append(f"by {creator_str}")
    if date:
        bits.append(f"dated {date}")
    line = "SOURCE RECORD: " + " ".join(bits) + ".\n"
    if creator_str:
        line += (
            f'When the text uses "the author", "I", "me", "my", or first '
            f"person, that refers to {creator_str}. Resolve such references to "
            f'the named person; never emit "the author" or a vague '
            f"first-person entity as a node.\n"
        )
    return line + "\n"


NODE_TYPES_V2 = [
    "person",
    "organisation",
    "project",
    "place",
    "event",
    "object",
    "document",
    "topic",
]

CATEGORIES_V2 = ["domain", "infrastructure"]


NODES_SCHEMA_V2 = {
    "type": "object",
    "required": ["nodes", "main_subject", "extraction_complete"],
    "properties": {
        "main_subject": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "node_type"],
                "properties": {
                    "name": {"type": "string"},
                    "node_type": {"type": "string", "enum": NODE_TYPES_V2},
                    "metadata": {"type": "object", "additionalProperties": True},
                },
            },
        },
        "codenames_to_resolve": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["codename", "refers_to"],
                "properties": {
                    "codename": {"type": "string"},
                    "refers_to": {"type": "string"},
                },
            },
        },
        "acronyms": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["acronym", "expansion"],
                "properties": {
                    "acronym": {"type": "string"},
                    "expansion": {"type": "string"},
                },
            },
        },
        "extraction_complete": {"type": "boolean"},
    },
}


# ORDINAL, NOT A SCORE. A claim's refs record that a node is MENTIONED, never
# that the claim is ABOUT it, so nothing distinguishes a setting from a subject
# and every downstream consumer inherits the ambiguity - an assembler choosing
# fifty claims from two thousand has nothing to rank with, and no rule can tell
# Sydney from Roswell because the edge is identical.
#
# Four values rather than a 0-1 score, deliberately: a float would wobble at
# least as much as the 3.9-point run-to-run noise measured on this corpus while
# presenting itself as exact, and a reviewer can disagree with `setting` in a
# way that means something where they cannot disagree with 0.62. Ordered, so a
# consumer can rank: subject > participant > setting > mentioned.
CLAIM_REF_ROLES = ("subject", "participant", "setting", "mentioned")


def build_claims_schema_v2(node_names: list[str]) -> dict:
    """JSON Schema for the claims pass. node_references items are restricted
    to the exact node names from Pass A - this is what physically prevents
    the model from introducing surface-form variants in claims.
    """
    return {
        "type": "object",
        "required": ["claims", "extraction_complete"],
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    # provenance_chain is REQUIRED: this is the forcing function
                    # (ADR 0044). Extraction runs under --json-schema, so a required
                    # field cannot be skipped - the model must answer "where did this
                    # come from?" at the moment it emits the claim. As an optional
                    # field it was simply never filled in.
                    "required": [
                        "content",
                        "category",
                        "claim_type",
                        "provenance_chain",
                        # Whether the text names who asserted it. DECLARED by the
                        # model that wrote the text - never derived downstream from
                        # origin_kind/attestation, which are only proxies for a
                        # property of the sentence and will eventually disagree
                        # with it (ADR 0044).
                        "attribution_in_text",
                    ],
                    "properties": {
                        "content": {"type": "string"},
                        "original_excerpt": {"type": "string"},
                        "category": {"type": "string", "enum": CATEGORIES_V2},
                        "claim_type": {
                            "type": "string",
                            "enum": list(VALID_CLAIM_TYPES),
                        },
                        "provenance_chain": {
                            "type": "object",
                            "required": ["origin_kind", "origin", "relay"],
                            "properties": {
                                "origin_kind": {
                                    "type": "string",
                                    "enum": list(VALID_ORIGIN_KINDS),
                                },
                                "origin": {"type": "string"},
                                # Record-scoped handle for a recurring anonymous
                                # actor - lets corroboration split two distinct
                                # whistleblowers inside one record. Never a
                                # cross-record identity.
                                "origin_ref": {"type": "string"},
                                "relay": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                        "attribution_in_text": {"type": "boolean"},
                        "attestation": {
                            "type": "string",
                            "enum": list(VALID_ATTESTATION),
                        },
                        "speaker": {"type": ["string", "null"]},
                        "location_in_record": {"type": "string"},
                        "date": {"type": "string"},
                        "node_references": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["name", "role"],
                                "properties": {
                                    "name": (
                                        {"type": "string", "enum": node_names}
                                        if node_names
                                        else {"type": "string"}
                                    ),
                                    "role": {
                                        "type": "string",
                                        "enum": list(CLAIM_REF_ROLES),
                                    },
                                },
                            },
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                },
            },
            "extraction_complete": {"type": "boolean"},
        },
    }


def _format_directory_v2(nodes: list[dict]) -> str:
    """Format the locked node directory for the claims-pass prompt."""
    lines = []
    for n in nodes:
        md = n.get("metadata") or {}
        extras = []
        if md.get("date_start"):
            extras.append(f"date_start={md['date_start']}")
        if md.get("date_end"):
            extras.append(f"date_end={md['date_end']}")
        extra_str = f" [{', '.join(extras)}]" if extras else ""
        node_type = n.get("node_type") or n.get("type", "?")
        lines.append(f"  - ({node_type:13}) {n['name']}{extra_str}")
    return "\n".join(lines)


# PINNED FOR THE LIFE OF A RUN. Prompts are read from disk per call and code is
# imported once, so editing a prompt mid-batch changes a running extraction
# while editing the schema beside it does not - one run picked up a new prompt
# and kept its old schema, and was told to emit a field that schema forbade.
# Capturing the text once makes the edit unable to reach a run in flight, which
# is better than a rule saying not to: cached chunks make a restart cheap, so
# nothing is lost by pinning.
_pinned_prompts: dict[str, str] = {}
_pinned_provenance: list[dict] = []


def pin_prompts() -> None:
    """Freeze this run's prompt text AND the provenance that describes it.

    Both halves, because pinning one is worse than pinning neither. The text
    was pinned and the provenance was not, so an edit landing mid-run left the
    run using the old prompt and STAMPING THE NEW ONE'S HASH into the digest -
    an artefact labelled with a configuration it was not produced under, which
    is the failure the fingerprint exists to prevent, caused by the fix for it.
    """
    _pinned_prompts.clear()
    _pinned_provenance.clear()
    for name, env in (
        ("nodes", "DIGESTER_NODES_PROMPT_FILE"),
        ("claims", "DIGESTER_CLAIMS_PROMPT_FILE"),
    ):
        text, prov = prompt_registry.resolve_prompt(name, env)
        _pinned_prompts[name] = text
        _pinned_provenance.append({"pass": name, **prov.as_dict()})


def release_prompts() -> None:
    _pinned_prompts.clear()
    _pinned_provenance.clear()


def _nodes_prompt() -> str:
    """Nodes-pass prompt, pinned for the life of a run, overridable per run via
    DIGESTER_NODES_PROMPT_FILE (lets Haiku and Sonnet carry different prompts).
    The version/hash actually used is recorded via prompt_provenance()."""
    return _pinned_prompts.get("nodes") or prompt_registry.prompt_text(
        "nodes", "DIGESTER_NODES_PROMPT_FILE"
    )


def _claims_prompt_template() -> str:
    """Claims-pass prompt template from the registry, overridable via
    DIGESTER_CLAIMS_PROMPT_FILE. Keeps the
    {directory}/{main_subject}/{codenames_block}/{acronyms_block} placeholders
    and {{ }} for literal braces."""
    return _pinned_prompts.get("claims") or prompt_registry.prompt_text(
        "claims", "DIGESTER_CLAIMS_PROMPT_FILE"
    )


def prompt_provenance() -> list[dict]:
    """Which prompt (id/version/sha256/file) each pass will use for this run,
    resolving the same env-override + registry path as the loaders above. Stamped
    into the digest so it is attributable to an exact prompt (ADR 0010 pattern)."""
    if _pinned_provenance:
        return [dict(p) for p in _pinned_provenance]
    nodes = prompt_registry.resolve_prompt("nodes", "DIGESTER_NODES_PROMPT_FILE")[1]
    claims = prompt_registry.resolve_prompt("claims", "DIGESTER_CLAIMS_PROMPT_FILE")[1]
    return [
        {"pass": "nodes", **nodes.as_dict()},
        {"pass": "claims", **claims.as_dict()},
    ]


def schema_fingerprint() -> str:
    """A hash over the SHAPES the model is constrained to, node names excluded.

    Node names vary per record and would make every digest's fingerprint
    unique; what identifies a configuration is the schema's structure - which
    fields are required, which enums exist, whether a reference is a string or
    an object carrying a role.
    """
    skeleton = json.dumps(
        [NODES_SCHEMA_V2, build_claims_schema_v2([]), CAST_SCHEMA, ACCOUNTS_SCHEMA],
        sort_keys=True,
    )
    return hashlib.sha256(skeleton.encode()).hexdigest()[:8]


def code_fingerprint() -> str:
    """A hash over the SOURCE that assembles a run, not the repository's state.

    Not decoration: prompt and schema together still miss a change to how the
    request is built - chunking, iteration, the directory carried between
    chunks. Two digests can share both halves and come from different code.

    It hashed the git commit plus a dirty flag, and that reports the wrong
    thing. A grid runs for hours while the repository keeps moving underneath
    it, so a commit touching only a report or a test relabelled every cell that
    came after it, and one grid looked like nine configurations. The reverse
    failure was worse: every uncommitted state hashed to the same "-dirty",
    so two runs with genuinely different code recorded the same fingerprint.

    Hashing the extraction sources instead is exact in both directions - it
    moves when the code that produces a claim moves, and only then. Tests,
    reports and prompts are excluded: prompts carry their own hash, and the
    others cannot reach a run.
    """
    import hashlib as _h
    from pathlib import Path

    roots = [Path(__file__).resolve().parent]
    try:
        import anomalica_common

        common = Path(anomalica_common.__file__).resolve().parent
        roots += [common / "llm", common / "digest"]
        roots += sorted(common.glob("pre_digest*"))
    except ImportError:
        pass

    digest = _h.sha256()
    for root in roots:
        if not root.exists():
            continue
        base = root.parent
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for f in files:
            if "__pycache__" in f.parts or f.name.startswith("test_"):
                continue
            digest.update(str(f.relative_to(base)).encode())
            digest.update(f.read_bytes())
    return digest.hexdigest()[:8]


def extraction_config() -> dict:
    """Everything that decides what a run produces, as one fingerprint.

    A DIGEST MUST RECORD WHAT ACTUALLY PRODUCED IT. The prompt sha names the
    prompt text and nothing else, so two digests sharing one can still have
    been built under different schemas or different code - and one of them
    was: a batch running while the prompt and schema were both edited picked
    up the new prompt (read from disk per call) and kept the old schema
    (imported once), producing 565 claims told to emit a field their schema
    forbade. Nothing in that artefact recorded the mismatch.

    `config` is the single value to compare. Two digests either provably came
    from the same setup or provably did not.
    """
    prompts = prompt_provenance()
    parts = [p.get("sha256", "") for p in prompts] + [
        schema_fingerprint(),
        code_fingerprint(),
    ]
    return {
        "config": hashlib.sha256("".join(parts).encode()).hexdigest()[:8],
        "prompts": prompts,
        "schema": schema_fingerprint(),
        "code": code_fingerprint(),
    }


# ---------------------------------------------------------------------------
# THE CAST PASS (ADR 0044 rebuild)
#
# One call over the WHOLE record - nodes AND sources together. Records were only
# ever cut into pieces because a single response cannot emit 400 claims; the
# INPUT always fitted (median record 12k tokens, the longest transcript 53k,
# against a 200k window). Cutting the nodes pass up is what caused entity
# fragmentation: the model named the film in piece 1, met it again in piece 3,
# and - reasoning locally, unable to see it was the same thing - named it
# differently. A model that has read every mention cannot do that.
#
# The same applies to sources, and more sharply: corroboration is decided by
# whether two claims share a source, so one anonymous source split in two by
# careless naming turns one rumour into two independent confirmations.
# ---------------------------------------------------------------------------

CAST_SCHEMA = {
    "type": "object",
    "required": ["main_subject", "nodes", "sources", "extraction_complete"],
    "properties": {
        "main_subject": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "node_type"],
                "properties": {
                    "name": {"type": "string"},
                    "node_type": {"type": "string", "enum": sorted(VALID_NODE_TYPES)},
                    "metadata": {"type": "object"},
                },
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "origin_kind", "origin", "relay"],
                "properties": {
                    "id": {"type": "string"},
                    "origin_kind": {
                        "type": "string",
                        "enum": list(VALID_ORIGIN_KINDS),
                    },
                    "origin": {"type": "string"},
                    "relay": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "codenames_to_resolve": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "codename": {"type": "string"},
                    "refers_to": {"type": "string"},
                },
            },
        },
        "acronyms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "acronym": {"type": "string"},
                    "expansion": {"type": "string"},
                },
            },
        },
        "extraction_complete": {"type": "boolean"},
    },
}


def _cast_prompt_template() -> str:
    return prompt_registry.prompt_text("cast", "DIGESTER_CAST_PROMPT_FILE")


def extract_cast(
    text: str,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
    use_api: bool = False,
) -> dict:
    """Resolve the whole record's cast in ONE call: nodes + sources.

    The document sits in a cached prefix; only the (small) task tail varies
    between iterations, so re-asking for more costs the tail plus the output,
    never the record again.
    """
    preamble = record_context + _cast_prompt_template()
    nodes: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    main_subject = ""
    codenames: dict[str, str] = {}
    acronyms: dict[str, str] = {}

    for it in range(ITERATION_MAX):
        task = "Emit the cast for this record: main_subject, nodes, sources."
        if nodes:
            task += (
                "\n\nALREADY CAPTURED - do NOT repeat these, and REUSE these exact"
                " names for anything you mention again:\n"
                + "\n".join(f"  - {n}" for n in sorted(nodes))
                + "\n\nSOURCES so far:\n"
                + "\n".join(
                    f"  - {sid}: {sv.get('origin')}"
                    for sid, sv in sorted(sources.items())
                )
                + "\n\nAdd only what is MISSING. If nothing is missing, return empty"
                " arrays and set extraction_complete=true."
            )

        raw = call_with_document(
            preamble, text, task, model, schema=CAST_SCHEMA, use_api=use_api
        )
        result = json.loads(raw) if isinstance(raw, str) else raw

        new_nodes = 0
        for n in result.get("nodes", []) or []:
            name = (n.get("name") or "").strip()
            if name and name not in nodes:
                nodes[name] = n
                new_nodes += 1
        new_sources = 0
        for sv in result.get("sources", []) or []:
            sid = (sv.get("id") or "").strip()
            if sid and sid not in sources:
                sources[sid] = sv
                new_sources += 1
        if not main_subject:
            main_subject = result.get("main_subject") or ""
        for c in result.get("codenames_to_resolve", []) or []:
            if c.get("codename"):
                codenames.setdefault(c["codename"], c.get("refers_to", ""))
        for a in result.get("acronyms", []) or []:
            if a.get("acronym"):
                acronyms.setdefault(a["acronym"], a.get("expansion", ""))

        if on_progress:
            done = " [model: complete]" if result.get("extraction_complete") else ""
            on_progress(
                f"    cast iter {it + 1}: +{new_nodes} nodes, +{new_sources} sources "
                f"(total {len(nodes)} nodes, {len(sources)} sources){done}"
            )

        if result.get("extraction_complete"):
            break
        if new_nodes == 0 and new_sources == 0:
            break

    return {
        "main_subject": main_subject,
        "nodes": list(nodes.values()),
        "sources": [sources[k] for k in sorted(sources)],
        "codenames_to_resolve": [
            {"codename": k, "refers_to": v} for k, v in codenames.items()
        ],
        "acronyms": [{"acronym": k, "expansion": v} for k, v in acronyms.items()],
    }


ACCOUNTS_SCHEMA = {
    "type": "object",
    "required": ["accounts", "extraction_complete"],
    "properties": {
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "subject", "span_start", "span_end"],
                "properties": {
                    "title": {"type": "string"},
                    "subject": {"type": "string"},
                    "when": {"type": "string"},
                    "where": {"type": "string"},
                    "span_start": {"type": "string"},
                    "span_end": {"type": "string"},
                    "also_spans": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "span_start": {"type": "string"},
                                "span_end": {"type": "string"},
                            },
                        },
                    },
                    "teller_role": {
                        "type": "string",
                        "enum": list(accounts_mod.VALID_TELLER_ROLES),
                    },
                },
            },
        },
        "extraction_complete": {"type": "boolean"},
    },
}


def _accounts_prompt_template() -> str:
    return prompt_registry.prompt_text("accounts", "DIGESTER_ACCOUNTS_PROMPT_FILE")


def extract_accounts(
    text: str,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
    use_api: bool = False,
) -> dict:
    """Find the distinct tellings in a record. The structural sibling of the cast.

    Same shape as `extract_cast` and for the same reason: the whole record sits
    in a cached prefix and only the task tail varies, so asking for more costs
    the tail and the output rather than the record again. Chunking would be
    wrong here in a way it is not for nodes - an account can straddle any
    boundary we choose, and a chunked pass would report one story as two
    whenever the split landed inside it.

    Iteration carries the titles found so far, not the spans: repeating a span
    back to the model invites it to adjust boundaries it already settled, and
    the boundary is the one thing this pass exists to establish.
    """
    preamble = record_context + _accounts_prompt_template()
    found: dict[str, dict] = {}

    for it in range(ITERATION_MAX):
        task = "Emit every distinct account in this record."
        if found:
            task += (
                "\n\nALREADY CAPTURED - do NOT repeat these and do NOT restate their"
                " spans:\n"
                + "\n".join(f"  - {t}" for t in sorted(found))
                + "\n\nAdd only accounts that are MISSING. If none are missing,"
                " return an empty array and set extraction_complete=true."
            )

        raw = call_with_document(
            preamble, text, task, model, schema=ACCOUNTS_SCHEMA, use_api=use_api
        )
        result = json.loads(raw) if isinstance(raw, str) else raw

        added = 0
        for a in result.get("accounts", []) or []:
            title = (a.get("title") or "").strip()
            if title and title not in found:
                found[title] = a
                added += 1

        if on_progress:
            done = " [model: complete]" if result.get("extraction_complete") else ""
            on_progress(
                f"    accounts iter {it + 1}: +{added} (total {len(found)}){done}"
            )
        if result.get("extraction_complete") or added == 0:
            break

    return {"accounts": list(found.values())}


def expand_source_ids(claims: list[dict], sources: list[dict]) -> int:
    """Deterministic post-process: source_id -> the full provenance_chain.

    No model. The chain was described ONCE, in the cast, so every claim citing a
    source gets byte-identical provenance - which is what stops one source
    fragmenting into several and inflating corroboration.
    """
    by_id = {s.get("id"): s for s in (sources or [])}
    resolved = 0
    for c in claims:
        src = by_id.get(c.get("source_id"))
        if not src:
            continue
        c["provenance_chain"] = {
            "origin_kind": src.get("origin_kind"),
            "origin": src.get("origin"),
            "relay": src.get("relay") or [],
        }
        resolved += 1
    return resolved


def build_claims_schema_v3(node_names: list[str], source_ids: list[str]) -> dict:
    """Claims schema for the rebuilt pipeline.

    A claim cites its source by ID - one short token - instead of re-describing
    the whole chain. Emitting a full chain object per claim made the model write
    the SAME chain twelve times for twelve claims from one email, which roughly
    doubled the claims output and is what pushed haiku past the call timeout,
    twice. It also let the model word the same source differently on different
    claims, fragmenting one source into several - and corroboration keys on that.
    """
    schema = build_claims_schema_v2(node_names)
    item = schema["properties"]["claims"]["items"]
    item["properties"].pop("provenance_chain", None)
    item["required"] = [r for r in item["required"] if r != "provenance_chain"]
    item["properties"]["source_id"] = (
        {"type": "string", "enum": source_ids} if source_ids else {"type": "string"}
    )
    item["required"].append("source_id")
    return schema


def extract_claims_v3(
    text: str,
    cast: dict,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
    use_api: bool = False,
) -> list[dict]:
    """Claims over the WHOLE record, held in a cached prefix.

    The document never moves. Coverage comes from varying the TASK TAIL - the
    cheap, uncached part - walking the model through the record, rather than from
    re-sending slices of it. Re-sending slices is what destroyed the cache: every
    piece is a new prefix, so it paid the 1.25x write and never earned the 0.1x
    read (measured: 390k written against 139k read).
    """
    nodes = cast.get("nodes", [])
    node_names = [n["name"] for n in nodes]
    sources = cast.get("sources", [])
    source_ids = [s["id"] for s in sources]
    schema = build_claims_schema_v3(node_names, source_ids)

    source_block = (
        "SOURCES - cite exactly one of these by id on every claim:\n"
        + "\n".join(
            f"  {s['id']}: {s.get('origin_kind')} - {s.get('origin')}"
            + (f"  (via {' -> '.join(s.get('relay') or [])})" if s.get("relay") else "")
            for s in sources
        )
    )
    preamble = record_context + _claims_prompt_template().format(
        directory=_format_directory_v2(nodes),
        main_subject=cast.get("main_subject", ""),
        codenames_block="",
        acronyms_block="",
    )

    claims: list[dict] = []
    seen: set = set()
    for it in range(ITERATION_MAX):
        task = source_block + "\n\nExtract factual claims from the document above."
        if claims:
            task += (
                "\n\nALREADY EXTRACTED - do not repeat these:\n"
                + "\n".join(
                    f"{i + 1}. {c['content']}  [source={c.get('source_id')}]"
                    for i, c in enumerate(claims)
                )
                + "\n\nExtract ADDITIONAL claims not in the list above, working"
                " through the parts of the record you have not covered yet. If none"
                " remain, return an empty array and set extraction_complete=true."
            )

        raw = call_with_document(
            preamble, text, task, model, schema=schema, use_api=use_api
        )
        result = json.loads(raw) if isinstance(raw, str) else raw

        new = 0
        for c in result.get("claims", []) or []:
            key = (
                (c.get("content") or "").strip().lower(),
                c.get("claim_type") or "",
                c.get("source_id") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            claims.append(c)
            new += 1

        if on_progress:
            done = " [model: complete]" if result.get("extraction_complete") else ""
            on_progress(f"    claims iter {it + 1}: +{new} (total {len(claims)}){done}")
        if result.get("extraction_complete"):
            break
        if new < ITERATION_MIN_NEW:
            break

    expand_source_ids(claims, sources)
    return claims


def extract_v3(
    text: str,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
    use_api: bool = False,
) -> dict:
    """The rebuilt pipeline: cast (whole record) -> claims (by source id) -> expand."""
    log = on_progress or (lambda _: None)
    log("Pass 1: the cast (whole record, one cached prefix)")
    cast = extract_cast(
        text,
        model=model,
        record_context=record_context,
        on_progress=log,
        use_api=use_api,
    )
    log(
        f"  {len(cast['nodes'])} nodes, {len(cast['sources'])} sources, "
        f"main_subject={cast.get('main_subject')!r}"
    )
    log("Pass 2: claims (same cached document, citing sources by id)")
    claims = extract_claims_v3(
        text,
        cast,
        model=model,
        record_context=record_context,
        on_progress=log,
        use_api=use_api,
    )
    log(f"  {len(claims)} claims")
    return {"cast": cast, "claims": claims, "nodes": cast["nodes"]}


# --- Cooperative cancel (the scheduler's hard-limit cancel; SIGTERM) ---
# A cancel sets a flag; the chunk loops check it BEFORE each model call and stop
# at that boundary. A signal handler only sets the flag, so an in-flight model
# call runs to completion and is stored in the call cache first (PEP 475 retries
# the interrupted wait) - the cancel costs nothing beyond finishing the current
# chunk, which is the whole point of the checkpoint cache. Completed chunks are on
# disk; a resume of the same (record, model, prompt, prep) replays them.
_cancel_requested = False


class ExtractionCancelled(Exception):
    """Raised at a chunk boundary when a cancel was requested. Not a failure - the
    completed chunks are cached, the CLI exits with a dedicated code, and the run
    is cheap to resume."""


def request_cancel() -> None:
    global _cancel_requested
    _cancel_requested = True


def reset_cancel() -> None:
    global _cancel_requested
    _cancel_requested = False


def _check_cancel() -> None:
    if _cancel_requested:
        raise ExtractionCancelled()


def extract_nodes_v2(
    text: str,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
    use_api: bool = False,
) -> dict:
    """Pass A of the v2 architecture: extract all nodes (no claims).

    Chunks the document, iterates per-chunk, threads a running directory
    across chunks. Returns a dict with keys:
        nodes: list[dict]            (deduplicated across chunks)
        main_subject: str
        codenames_to_resolve: list[dict]
        acronyms: list[dict]
    """
    chunks = _build_chunks(text)
    merged_nodes: dict[str, dict] = {}  # name -> node dict
    main_subject = ""
    codenames: dict[str, str] = {}
    acronyms: dict[str, str] = {}

    for ci, chunk in enumerate(chunks):
        if on_progress and len(chunks) > 1:
            on_progress(f"  nodes chunk {ci + 1}/{len(chunks)} ({len(chunk):,} chars)")

        seen_names_in_chunk: set[str] = set()
        for it in range(ITERATION_MAX):
            _check_cancel()  # stop before dispatching the next call; prior calls cached
            # The directory grows with every chunk and travels in the prompt,
            # so a route that cannot carry it fails HERE, mid-book, with
            # malformed JSON - not at Pass B where the enum guard sits. Checked
            # each round so the reroute happens the moment the directory
            # outgrows the route rather than after another twenty minutes.
            check_route_capacity(model, len(merged_nodes))
            directory_lines = [
                f"  - ({n['node_type']}) {name}" for name, n in merged_nodes.items()
            ]
            prompt = record_context + _nodes_prompt()
            if directory_lines:
                prompt = (
                    "EXISTING NODE DIRECTORY from previous chunks/rounds - "
                    "use these EXACT names where they apply; do NOT emit variants:\n"
                    + "\n".join(directory_lines)
                    + "\n\n"
                    + prompt
                )
            raw = _call(prompt, chunk, model, schema=NODES_SCHEMA_V2, use_api=use_api)
            result = json.loads(raw) if isinstance(raw, str) else raw

            new_in_round = 0
            for n in result.get("nodes", []):
                if n["name"] in merged_nodes:
                    continue
                merged_nodes[n["name"]] = n
                seen_names_in_chunk.add(n["name"])
                new_in_round += 1

            if result.get("main_subject") and not main_subject:
                main_subject = result["main_subject"]
            for c in result.get("codenames_to_resolve", []):
                codenames.setdefault(c["codename"], c["refers_to"])
            for a in result.get("acronyms", []):
                acronyms.setdefault(a["acronym"], a["expansion"])

            if on_progress:
                done = " [model: complete]" if result.get("extraction_complete") else ""
                on_progress(
                    f"    nodes iter {it + 1}: +{new_in_round} nodes "
                    f"(total {len(merged_nodes)}){done}"
                )
            if result.get("extraction_complete"):
                break
            if new_in_round < ITERATION_MIN_NEW:
                break

    return {
        "nodes": list(merged_nodes.values()),
        "main_subject": main_subject,
        "codenames_to_resolve": [
            {"codename": k, "refers_to": v} for k, v in codenames.items()
        ],
        "acronyms": [{"acronym": k, "expansion": v} for k, v in acronyms.items()],
    }


def extract_claims_v2(
    text: str,
    nodes_pass_result: dict,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
    use_api: bool = False,
) -> list[dict]:
    """Pass B of the v2 architecture: extract claims, constrained to using
    only the node names from nodes_pass_result. Chunks the document and
    iterates per chunk. Returns a flat list of claim dicts (each with
    category=domain|infrastructure).
    """
    nodes = nodes_pass_result.get("nodes", [])
    node_names = [n["name"] for n in nodes]
    main_subject = nodes_pass_result.get("main_subject") or "(unspecified)"
    codenames = nodes_pass_result.get("codenames_to_resolve") or []
    acronyms = nodes_pass_result.get("acronyms") or []

    codenames_block = ""
    if codenames:
        codenames_block = (
            "CODENAMES TO RESOLVE (do not emit as nodes; resolve in claim text):\n"
        )
        for c in codenames:
            codenames_block += f"  - {c['codename']} -> {c['refers_to']}\n"
    acronyms_block = ""
    if acronyms:
        acronyms_block = "ACRONYM GLOSSARY (expand on first use in claim text):\n"
        for a in acronyms:
            acronyms_block += f"  - {a['acronym']} = {a['expansion']}\n"

    schema = build_claims_schema_v2(node_names)
    directory = _format_directory_v2(nodes)

    chunks = _build_chunks(text, max_chars=CLAIMS_CHUNK_MAX_CHARS)
    merged_claims: list[dict] = []
    # Keyed on the proposition AND its provenance, and global across chunks. A
    # content-only key silently dropped the LATER of two assertions of the same
    # proposition - and the later one is usually the one that finally names its
    # source. See _claim_key_v2.
    seen_content: set[tuple[str, str, str, str]] = set()

    for ci, chunk in enumerate(chunks):
        if on_progress and len(chunks) > 1:
            on_progress(f"  claims chunk {ci + 1}/{len(chunks)} ({len(chunk):,} chars)")

        chunk_claims: list[dict] = []
        for it in range(ITERATION_MAX):
            _check_cancel()  # stop before dispatching the next call; prior calls cached
            prompt = record_context + _claims_prompt_template().format(
                directory=directory,
                main_subject=main_subject,
                codenames_block=codenames_block,
                acronyms_block=acronyms_block,
            )
            if chunk_claims:
                exclude = "\n".join(
                    f"{i + 1}. {c['content']}  [type={c.get('claim_type') or '-'}"
                    f"; speaker={c.get('speaker') or '-'}"
                    f"; attestation={c.get('attestation') or '-'}]"
                    for i, c in enumerate(chunk_claims)
                )
                prompt += (
                    "\n\nALREADY EXTRACTED CLAIMS - each shown with the provenance it was "
                    "captured under. Do NOT repeat any of these:\n"
                    + exclude
                    + "\n\nPROVENANCE EXCEPTION - this overrides the no-repeat rule. A claim is "
                    "a repeat only when the proposition AND its provenance match a line above. "
                    "The same proposition asserted under DIFFERENT provenance is a SEPARATE "
                    "claim and you MUST extract it - specifically when the source now names an "
                    "originating source, an intermediary, or a document it did not name before, "
                    "or when the evidential standing differs (a bare assertion earlier, the same "
                    "assertion now attributed to someone). Never suppress an attributed "
                    "assertion because an unattributed version was captured earlier - the "
                    "attribution is the point."
                    "\n\nExtract ADDITIONAL factual claims from this chunk that are not in the list above. "
                    "If only trivial or redundant claims would remain, return an empty claims array "
                    "and set extraction_complete=true."
                )

            raw = _call(prompt, chunk, model, schema=schema, use_api=use_api)
            result = json.loads(raw) if isinstance(raw, str) else raw

            new_in_round = 0
            for c in result.get("claims", []):
                key = _claim_key_v2(c)
                if key in seen_content:
                    continue
                seen_content.add(key)
                chunk_claims.append(c)
                merged_claims.append(c)
                new_in_round += 1

            if on_progress:
                done = " [model: complete]" if result.get("extraction_complete") else ""
                on_progress(
                    f"    claims iter {it + 1}: +{new_in_round} claims "
                    f"(chunk total {len(chunk_claims)}, doc total {len(merged_claims)}){done}"
                )
            if result.get("extraction_complete"):
                break
            if new_in_round < ITERATION_MIN_NEW:
                break

    return merged_claims


def extract_two_pass(
    text: str,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
    use_api: bool = False,
) -> dict:
    """Top-level v2 entry point. Runs nodes pass then claims pass. Returns
    a dict with keys: nodes, claims, main_subject, codenames_to_resolve,
    acronyms. cli.py consumes this and writes the YAML digest.

    `use_api` is the resolved per-component metered toggle (the digester resolves
    DIGESTER_USE_API), threaded to every model call.
    """
    reset_cancel()  # a cancel from a prior run in this process must not carry over
    # Extract from the pre-digest (ADR 0042): all deterministic model-prep. The
    # caller materialises + stores the pre-digest and records its hash; this is
    # idempotent, so a raw-text caller (benchmarks) still gets the same input.
    text = materialise(text)
    pin_prompts()
    if on_progress:
        on_progress("Pass A: nodes (with iteration + chunking)")
    nodes_result = extract_nodes_v2(
        text,
        model=model,
        record_context=record_context,
        on_progress=on_progress,
        use_api=use_api,
    )
    if on_progress:
        on_progress(
            f"  {len(nodes_result['nodes'])} nodes, {len(nodes_result['acronyms'])} acronyms, "
            f"main_subject={nodes_result['main_subject']!r}"
        )

    if on_progress:
        on_progress(
            "Pass B: claims (constrained to locked nodes, with iteration + chunking)"
        )
    claims = extract_claims_v2(
        text,
        nodes_result,
        model=model,
        record_context=record_context,
        on_progress=on_progress,
        use_api=use_api,
    )
    if on_progress:
        n_dom = sum(1 for c in claims if c.get("category") == "domain")
        n_inf = sum(1 for c in claims if c.get("category") == "infrastructure")
        on_progress(
            f"  {len(claims)} claims total: {n_dom} domain, {n_inf} infrastructure"
        )

    return {
        "nodes": nodes_result["nodes"],
        "main_subject": nodes_result["main_subject"],
        "codenames_to_resolve": nodes_result["codenames_to_resolve"],
        "acronyms": nodes_result["acronyms"],
        "claims": claims,
        "prompt_provenance": prompt_provenance(),
    }


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
        # BOTH SHAPES. Refs were bare names before roles existed, and a cached
        # response or an older prompt still returns them that way; a run must
        # not lose its refs over a shape change it did not make.
        ref_roles = {}
        flat_refs = []
        for r in refs:
            if isinstance(r, dict):
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                flat_refs.append(name)
                role = r.get("role")
                if role in CLAIM_REF_ROLES:
                    ref_roles[name] = role
            elif r:
                flat_refs.append(str(r))
        refs = flat_refs
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
                ref_roles=ref_roles or None,
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
        extraction_complete=bool(data.get("extraction_complete", False)),
    )
