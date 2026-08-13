#!/usr/bin/env python3
"""Trial: split extraction into nodes-only pass then claims-only pass.

Goal: prove that splitting the single combined "nodes + claims in one
output" pass into TWO focused passes (one for nodes, one for claims with
node_references constrained to the locked pass-1 list) fixes the
within-output duplicate-node problem we've been hitting.

Architecture under test (matches the conclusion of the 2026-05-25 discussion):

  Pass 1: NODES ONLY
    - Returns ALL node names with canonical forms (acronyms expanded,
      country prefixes consistent, dedup enforced)
    - Returns main_subject (the document's principal entity, used by
      anchoring)
    - Returns codenames the model should resolve in claims rather than
      emit as nodes
    - Returns acronym glossary
    - 8 node types: person, organisation, project, place, event, object,
      document, concept (pattern is NOT extractor-emitted; pattern nodes
      are curator-created)
    - "project" is the collapsed type for what was previously matter +
      programme + investigation

  Pass 2: CLAIMS ONLY
    - Receives the locked node list from Pass 1
    - Returns claims, each with:
        - category: domain | infrastructure
        - claim_type, attestation, content, original_excerpt, etc
        - node_references USING ONLY the names from Pass 1
    - "infrastructure" = source-graph cross-references (X cites Y,
      X recommends Z, X interviews Y) that we don't want to publish on the
      public site but DO want to capture for content discovery
    - "domain" = everything else (phenomenal content, witness accounts,
      career bios, organisational facts)

Run inside the digester container.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digester.extract import _call_cli  # noqa: E402
from digester.record_parser import parse_record  # noqa: E402


SLOT_WORD = sys.argv[4] if len(sys.argv) > 4 else "concept"  # override via CLI arg
assert SLOT_WORD in ("concept", "principle", "topic"), (
    f"unsupported slot word: {SLOT_WORD}"
)

VALID_NODE_TYPES = [
    "person",
    "organisation",
    "project",
    "place",
    "event",
    "object",
    "document",
    SLOT_WORD,
]

CLAIM_TYPES = [
    "observation",
    "testimony",
    "hearsay",
    "opinion",
    "measurement",
    "administrative",
]
ATTESTATION = ["first_hand", "second_hand", "third_hand"]
CATEGORIES = ["domain", "infrastructure"]


NODES_PROMPT = """You are extracting the COMPLETE NODE DIRECTORY for a knowledge graph from a single document.

Your ONLY job in this call is to identify every distinct named entity in the document and return ONE canonical record per real-world thing. You are NOT extracting claims in this call - that is a separate pass.

================================================================
NODE TYPES (eight - choose one per node)
================================================================

- "person": a named human individual. Format "Last, First Middle". No titles/ranks/honourifics. Pseudonyms and single-name historical figures stay as-is. Do NOT create person nodes for redacted/anonymous actors ("USS Louisville Officer (redacted)") - emit the relevant organisation instead.

- "organisation": a named acting BODY - government agencies, military units, companies, research institutes, publications, news outlets, committees, standing offices, foundations. Has its own identity, staff, address, or institutional continuity.

- "project": a NAMED time-bounded or initiative-bounded effort - programmes, investigations, operations, research projects, official inquiries. AATIP, Project Apollo, Project Blue Book, AAWSAP, the Condon Committee inquiry, the AARO Historical Record review, the Manhattan Project. Distinguished from organisation: a project is the WORK; an organisation is the BODY that runs it. The US Air Force is an organisation; Project Blue Book is a project the Air Force ran.

- "place": a named geographic location. Format "Country, Region, Specific" largest-unit-first. "USA, Nevada, Area 51". Do NOT extract countries/states/regions as places.

- "event": a discrete or bounded-in-time occurrence. Has at least a start year. Can span hours, days, months, years - use date_start (required) and optionally date_end. The Nimitz UAP encounter (2004-11-10 to 2004-11-16) is ONE event. AARO's HR2 investigation 1952-1969 could be an event-as-period.

- "object": a specific named PHYSICAL thing - craft, vessel, vehicle, sample, device, named building. Must pass the touch test (you could imagine reaching out and touching it). Phenomena, effects, signatures all FAIL the touch test - not objects.

- "document": a written or recorded artefact - book, report, paper, FOIA release, video footage, podcast episode, article, memo, testimony, affidavit.

- "__SLOT__": a RECOGNISED named idea, theory, principle, or phenomenon that exists independent of this document (general relativity, the Pais Effect, anti-gravity propulsion, zero-point energy). NOT a specific named alleged craft, vehicle, or physical thing (TR-3B is NOT a __SLOT__ - it is an alleged craft, classify as object or document depending on whether the source treats it as a real thing or a rumour). NOT generic touchable nouns (gravity, plasma, electromagnetism are too generic). NOT mechanisms lifted from patent jargon. NOT vague catch-alls ("the big secret"). NOT ad-hoc theories named only within this document.

NOTE: there is no "matter" type and no "pattern" type for extraction. The "matter" type is deprecated and the eight types above cover everything we extract. If you observe a recurring shape across cases described in the source (e.g. "X has happened repeatedly"), that observation goes in a CLAIM about whichever node the source attaches it to - patterns are not their own nodes during extraction.

If a candidate doesn't fit any of the eight types above, it's probably not a node - it's information that should be expressed as a claim attached to an existing node.

================================================================
PORTABILITY (the card test)
================================================================

Every node name must be identifiable on its own, out of context. "the testimony", "the hearing", "the report" all FAIL the card test - include enough specificity (date, parties, subject) that the name stands alone.

================================================================
ACRONYM EXPANSION
================================================================

In node names, expand acronyms as "Full Name (ACRONYM)":
  - AATIP -> "Advanced Aerospace Threat Identification Program (AATIP)"
  - AARO -> "All-Domain Anomaly Resolution Office (AARO)"
  - DIA -> "Defense Intelligence Agency (DIA)"
  - VFA-41 -> "Strike Fighter Squadron 41 (VFA-41)"
  - CSG-11 -> "Carrier Strike Group 11 (CSG-11)"
  - NAVAIR -> "Naval Air Systems Command (NAVAIR)"
  - LIGO -> "Laser Interferometer Gravitational-Wave Observatory (LIGO)"

EXCEPTION - SAFE ACRONYMS (use bare, never expand): UFO, UAP, CIA, FBI, NSA, NASA, DoD, FAA, NATO, UN, EU, US, USA, UK, USSR, GPS, TV, CPU, GPU, USB, URL, API.

================================================================
DEDUPLICATION - this is the most important rule
================================================================

Each real-world entity appears as ONE node only. Multiple nodes for the same entity is unacceptable.

After identifying all candidate nodes, sort them mentally and check for duplicates BEFORE emitting your output. The following variation classes MUST be merged into ONE entry:

  (a) Acronym suffix present vs absent: "Defense Intelligence Agency" + "Defense Intelligence Agency (DIA)" - SAME entity, emit ONCE with the acronym suffix.
  (b) Country prefix variants: "Navy" + "US Navy" + "United States Navy" - SAME entity, emit ONCE as "United States Navy" (or with safe-acronym "US Navy" if that is the only form the document uses; pick ONE).
  (c) Department of Defense variants: "DoD" + "Department of Defense" + "Department of Defense (DoD)" + "United States Department of Defense" - SAME entity, emit ONCE.
  (d) US/UK spelling variants: "Naval Air Warfare Center" + "Naval Air Warfare Centre" - SAME entity, emit ONCE (use the spelling the document uses).
  (e) Long-form vs short-form of the same body: "House Oversight Subcommittee on Cybersecurity" + "Subcommittee on Cybersecurity" + "the subcommittee" - SAME entity, emit ONCE with the long portable form.
  (f) Descriptor parentheses variants: "Project Unity" + "Project Unity (podcast)" - SAME entity, emit ONCE.

The cost of emitting two variants is far higher than the cost of merging - aliases can always be added later, but duplicate nodes contaminate the graph.

================================================================
ALSO RETURN: main_subject, codenames, acronyms
================================================================

Beyond the nodes list, your response includes three small fields:

  - main_subject: the canonical NAME of the one node (from your nodes list above) that is the document's principal subject. Used by downstream claim-extraction to anchor claims that are specifically about this subject. Must be one of the names you emitted.

  - codenames_to_resolve: callsigns and military codenames (FASTEAGLE 01, FASTEAGLE 02, Tic Tac, Fast Walker) that should NEVER become person/object nodes - they are operational shorthand. For each, name the real entity it refers to (from your nodes list). The claims pass will resolve "FASTEAGLE 01" to "F/A-18F flown by David Fravor" in claim text.

  - acronyms: every domain-specific acronym the document uses, with its expansion. Used by the claims pass to expand acronyms on first use inside claim prose.

================================================================
OUTPUT FORMAT (valid JSON only, no markdown fencing)
================================================================

{
  "main_subject": "canonical name from nodes list below",
  "nodes": [
    {
      "name": "canonical portable name",
      "node_type": "person|organisation|project|place|event|object|document|__SLOT__",
      "metadata": {
        "date_start": "YYYY-MM-DD or YYYY-MM or YYYY (events only)",
        "date_end": "YYYY-MM-DD or YYYY-MM or YYYY (events only, if bounded)"
      }
    }
  ],
  "codenames_to_resolve": [
    {"codename": "FASTEAGLE 01", "refers_to": "canonical name from nodes list"}
  ],
  "acronyms": [
    {"acronym": "AAV", "expansion": "Anomalous Aerial Vehicle"}
  ]
}

Before you emit: walk your nodes list one more time and check for the duplicate-variation classes above. If you find any pair that would merge, MERGE them before emitting.
"""


NODES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "main_subject"],
    "properties": {
        "main_subject": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "node_type"],
                "properties": {
                    "name": {"type": "string"},
                    "node_type": {"type": "string", "enum": VALID_NODE_TYPES},
                    "metadata": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            },
        },
        "codenames_to_resolve": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
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
                "additionalProperties": False,
                "required": ["acronym", "expansion"],
                "properties": {
                    "acronym": {"type": "string"},
                    "expansion": {"type": "string"},
                },
            },
        },
    },
}


CLAIMS_PROMPT_TEMPLATE = """You are extracting EVERY factual claim from a document for a knowledge graph. A previous pass has already identified all named entities (the NODE DIRECTORY below) - your job is to extract atomic factual claims, each referencing only those existing nodes.

================================================================
NODE DIRECTORY - use ONLY these names in node_references
================================================================

The following nodes have ALREADY been extracted for this document. Each claim's node_references field must use the EXACT names listed below. Do NOT introduce new node names or surface-form variants in node_references - if a claim mentions an entity, find it in this list and use that name.

{directory}

MAIN SUBJECT of the document: {main_subject}
  Use this name verbatim as the anchor for claims that are specifically about the main subject (see anchoring rules below).

{codenames_block}

{acronyms_block}

================================================================
CLAIM CATEGORIES (every claim gets one)
================================================================

Each claim must be tagged with category:

- "domain" (most claims): facts about UAP, witnesses, encounters, investigations, careers, programmes, organisations, biographical facts, observations, measurements, official statements, findings. "David Fravor was a Strike Fighter Squadron 41 (VFA-41) commander" = domain. "The aerial vehicle accelerated to approximately 19,500 kilometres per hour" = domain. "Senator Reid sponsored the AATIP appropriation in 2007" = domain.

- "infrastructure" (the source-graph): claims whose purpose is to point at OTHER content - X cites Y, X recommends Z, X interviewed Y, X appeared on Z's podcast, X mentions Y's book. "Coulthart cites Vallée's Passport to Magonia in chapter 23" = infrastructure. "X appeared on Joe Rogan's podcast to promote their book" = infrastructure. "Coulthart says you should read Y's research" = infrastructure. These will not be published on the public site (to avoid endorsement-by-default) but are kept for content discovery.

The rule of thumb: if the claim's purpose is "look at this other content" or "X is recommending/citing Y", it is infrastructure. Otherwise it is domain.

================================================================
CLAIM TYPES + ATTESTATION
================================================================

claim_type: observation | testimony | hearsay | opinion | measurement | administrative
attestation: first_hand | second_hand | third_hand

================================================================
ATOMIC CLAIMS RULE
================================================================

One assertion per claim. Split compound statements. Specifically, do NOT bundle "who this person is" with "what they did" - those are TWO claims. "George Knapp, a journalist at KLAS-TV, published the memo in June 2019" splits into:
  Claim 1: "George Knapp is a journalist at KLAS-TV in Las Vegas."
  Claim 2: "In June 2019, George Knapp published the Harry Reid 2009 SAP Memo."

Same for "X arrived at place B, where they met Y" - split into "X arrived at place B" + "X met Y at place B".

================================================================
ANCHORING
================================================================

If a claim is specifically ABOUT the main subject (see node directory above), anchor it with the main subject's exact name as a temporal/contextual scene-setter ("During X, ..." or "In X, ..."). Do NOT use "X states that..." / "X testified that..." anchoring - that turns claims into reported speech and duplicates the metadata's job. The claim should BE the assertion.

If the claim is NOT specifically about the main subject (background, peripheral facts, a person's career fact), write it naturally with no anchor prefix.

Forbidden anchor verbs at the head of a claim: "X states that", "X says that", "X testified that", "X declared that", "X announced that", "X reported that". Replace with a positional preposition or no anchor at all.

================================================================
PERSON REFERENCES IN CLAIM TEXT
================================================================

Use the full natural-order name inside claim text ("Luis Elizondo", "David Fravor") - NOT a surname-only shortcut. node_references uses the canonical "Last, First" form from the directory.

================================================================
UNIT NORMALISATION
================================================================

Convert all measurements in "content" to metric units with full names: "10 metres" not "10m", "24,000 metres" not "24km", "1,200 kilometres per hour" not "1200 km/h". Preserve original precision - "about 80,000 feet" becomes "approximately 24,000 metres" (rounded), NOT "24,384 metres".

The original_excerpt preserves source phrasing verbatim (including original units).

================================================================
ISO DATES MANDATORY EVERYWHERE
================================================================

Claim text uses ISO dates: "2004-11-14" not "14 November 2004". Original_excerpt preserves source phrasing.

================================================================
ACRONYM EXPANSION IN CLAIM TEXT
================================================================

Expand each acronym on first use in a claim: "Anomalous Aerial Vehicle (AAV)", "forward-looking infrared (FLIR)". Subsequent uses in the same claim are bare. SAFE acronyms (UFO, UAP, CIA, etc) are always bare.

================================================================
EXHAUSTIVE EXTRACTION
================================================================

Do not summarise or curate. Capture every factual statement - dates, names, places, quoted figures, asides, parenthetical remarks. A 300-page book contains thousands of claims, not dozens. Coverage matters more than highlighting.

================================================================
OUTPUT FORMAT (valid JSON only, no markdown fencing)
================================================================

{{
  "claims": [
    {{
      "content": "normalised assertion with metric units",
      "original_excerpt": "exact original wording from the source",
      "category": "domain|infrastructure",
      "claim_type": "observation|testimony|hearsay|opinion|measurement|administrative",
      "attestation": "first_hand|second_hand|third_hand",
      "speaker": "person name from directory, or null",
      "location_in_record": "page 3, paragraph 2, or timestamp",
      "date": "YYYY-MM-DD if applicable",
      "node_references": ["Node A from directory", "Node B from directory"],
      "confidence": 1.0
    }}
  ],
  "extraction_complete": true
}}
"""


def build_claims_schema(node_names: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["claims", "extraction_complete"],
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["content", "category", "claim_type", "attestation"],
                    "properties": {
                        "content": {"type": "string"},
                        "original_excerpt": {"type": "string"},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "claim_type": {"type": "string", "enum": CLAIM_TYPES},
                        "attestation": {"type": "string", "enum": ATTESTATION},
                        "speaker": {"type": ["string", "null"]},
                        "location_in_record": {"type": "string"},
                        "date": {"type": "string"},
                        "node_references": {
                            "type": "array",
                            "items": {"type": "string", "enum": node_names},
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


def format_directory_block(nodes: list[dict]) -> str:
    lines = []
    for n in nodes:
        md = n.get("metadata") or {}
        extras = []
        if md.get("date_start"):
            extras.append(f"date_start={md['date_start']}")
        if md.get("date_end"):
            extras.append(f"date_end={md['date_end']}")
        extra_str = f" [{', '.join(extras)}]" if extras else ""
        lines.append(f"  - ({n['node_type']:13}) {n['name']}{extra_str}")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: trial_nodes_then_claims.py <input.md> <output.json>",
            file=sys.stderr,
        )
        return 1
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    model = sys.argv[3] if len(sys.argv) > 3 else "opus"

    parsed = parse_record(input_path.read_text())
    body = parsed.body
    print(f"input: {input_path} ({len(body):,} chars)")
    print(f"model: {model}")
    print(f"slot word: {SLOT_WORD}")

    # Substitute slot word into prompt at runtime.
    nodes_prompt = NODES_PROMPT.replace("__SLOT__", SLOT_WORD)

    print("\n=== PASS 1: nodes ===")
    t0 = time.time()
    raw1 = _call_cli(nodes_prompt, body, model, schema=NODES_SCHEMA)
    pass1 = json.loads(raw1) if isinstance(raw1, str) else raw1
    print(f"  elapsed: {time.time() - t0:.0f}s")
    print(f"  nodes: {len(pass1.get('nodes', []))}")
    print(f"  codenames_to_resolve: {len(pass1.get('codenames_to_resolve', []))}")
    print(f"  acronyms: {len(pass1.get('acronyms', []))}")
    print(f"  main_subject: {pass1.get('main_subject')!r}")

    nodes = pass1.get("nodes", [])
    node_names = [n["name"] for n in nodes]

    print("\n=== PASS 2: claims ===")
    codenames = pass1.get("codenames_to_resolve", [])
    codenames_block = ""
    if codenames:
        codenames_block = (
            "CODENAMES TO RESOLVE (do not emit as nodes; resolve in claim text):\n"
        )
        for c in codenames:
            codenames_block += f"  - {c['codename']} -> {c['refers_to']}\n"
    acronyms = pass1.get("acronyms", [])
    acronyms_block = ""
    if acronyms:
        acronyms_block = "ACRONYM GLOSSARY (expand on first use in claim text):\n"
        for a in acronyms:
            acronyms_block += f"  - {a['acronym']} = {a['expansion']}\n"

    claims_prompt = CLAIMS_PROMPT_TEMPLATE.format(
        directory=format_directory_block(nodes),
        main_subject=pass1.get("main_subject") or "(none identified)",
        codenames_block=codenames_block,
        acronyms_block=acronyms_block,
    )

    t0 = time.time()
    raw2 = _call_cli(claims_prompt, body, model, schema=build_claims_schema(node_names))
    pass2 = json.loads(raw2) if isinstance(raw2, str) else raw2
    print(f"  elapsed: {time.time() - t0:.0f}s")
    claims = pass2.get("claims", [])
    print(f"  claims: {len(claims)}")
    domain_n = sum(1 for c in claims if c.get("category") == "domain")
    infra_n = sum(1 for c in claims if c.get("category") == "infrastructure")
    print(f"    domain: {domain_n}")
    print(f"    infrastructure: {infra_n}")

    out = {
        "model": model,
        "input_path": str(input_path),
        "pass1": pass1,
        "pass2": pass2,
    }
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"\nwrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
