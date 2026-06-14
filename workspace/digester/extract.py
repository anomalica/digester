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

The knowledge graph uses these node types (eight total - "matter" is no longer one of them; see below for where the old "matter" content goes):

- "person": a named human individual.

- "organisation": a named acting entity - government bodies, military units, companies, research groups, agencies, publications, news outlets, committees, programmes, investigations, foundations, advocacy groups. Includes both ongoing standing bodies (the US Department of Defense, the New York Times) AND named time-bounded operational efforts (AATIP, Project Blue Book, the Condon Committee). For the latter, set the optional `metadata.kind` field to one of: `programme` (a funded operational structure with named scope and staff, e.g. AATIP, Stargate, Project Apollo), `investigation` (a probe with a defined question and concluding output, e.g. Project Blue Book, the Condon Committee, an Inspector General review), `agency` (standing government body), `unit` (named military or operational unit), `committee` (named oversight/investigation committee), `publication` (named newspaper, journal, podcast, book series), or another short kebab-case descriptor if none of those fit. When unsure, omit `kind` and just emit the bare organisation.

- "place": a named geographic location. Use "Country, Region, City" or "Country, Feature" format - largest geographic unit first (e.g. "USA, Nevada, Area 51" not "Area 51"; "Australia, Queensland, Tully" not "Tully Queensland"; "Mexico, Gulf of Mexico" not "Gulf of Mexico"). For features that span countries (Persian Gulf, Bermuda Triangle), use the region: "Middle East, Persian Gulf".

- "event": a discrete or bounded-in-time thing that happened. MUST have a start date (ISO format, at least a year). Events can be instantaneous OR span a period of hours, days, weeks, months, or years - use `metadata.date_start` (mandatory) and optionally `metadata.date_end` for bounded periods. The Nimitz UAP encounter (2004-11-10 to 2004-11-16) is ONE event spanning seven days. AATIP's operational period (2007 to 2012) is ONE event if you want to refer to the programme's lifespan as a temporal subject (though the programme itself is an `organisation` node with `kind: programme`). The Watergate inquiry (1972 to 1974) is one event-as-period. Use event when you want to refer to "the thing that happened across this stretch of time"; use organisation when you want to refer to "the body that did things during that time". Both can coexist for the same real-world activity (the AATIP programme = organisation; the AATIP operational period = event).

- "object": a specific named PHYSICAL thing you could literally touch or point at. The touch test - if you cannot imagine reaching out and putting your hand on it, it is NOT an object. Craft, materials, devices, samples, sensors, weapons, named buildings (only as objects when the physical structure is the subject), ships, aircraft, vehicles all pass. Phenomena, disturbances, events, effects, hypotheses, observations, video footage all FAIL the touch test - "water disturbance" is not an object (you can't touch a disturbance, only the water during it), "a flash of light" is not an object, "the radar return" is not an object, "the glow" is not an object. Documents are also NOT objects (use document type).

- "document": a written or recorded artefact (memo, report, letter, article, paper, book, briefing, video footage, slides, statement, testimony, affidavit, Freedom of Information Act release). Always use this for textual or recorded artefacts, never "object".

- "concept": a RECOGNISED named idea, theory, principle, or phenomenon that exists independent of this document - something a reader could look up and find defined elsewhere (general relativity, special relativity, gravitational waves, superconductivity, zero-point energy, anti-gravity propulsion, nuclear fusion, the Pais Effect, vacuum polarisation). A concept may be referenced without being asserted (general relativity is referenced, never argued) and is still extracted. STRICT EXCLUSIONS - do NOT emit a concept for: (a) anything touchable - that is an object ("room temperature superconductor" = object; "room temperature superconductivity" = concept; rule: "X device/reactor/craft" is an object, "X" the principle is the concept); (b) anything tied to a specific time - that is an event or organisation; (c) a person, place, or organisation; (d) an effort people run over time (research, a programme, an investigation) - that is an organisation with `kind: programme` or `kind: investigation`; (e) a vague catch-all where almost anything could carry the label ("the big secret", "the phenomenon", "disclosure of the truth"); (f) jargon or a mechanism lifted from quoted technical/patent text that is not a recognised standalone idea ("non-linear scattering of RF and sonar signals", "vacuum/plasma bubble sheath"); (g) a claimed capability or consequence ("asteroid deflection", "electricity grid revolution"); (h) an ad-hoc theory named only within this document and not recognised outside it ("the test-flight theory"). Merge synonyms to ONE concept (superluminal travel = faster-than-light travel = warp speed: one node, the rest aliases).

- "pattern": a recurring shape observed across cases - either across multiple cases within this document, OR across multiple documents in the corpus. Examples: shifting official accounts of anomalous events, UAP observed near nuclear facilities, witness intimidation after sightings, biological effects on witnesses, document destruction after sensitive events. A pattern is the SHAPE, not any single instance: "Roswell secrecy" is an event; "the recurring shape of denial-then-partial-acknowledgement across UAP cases" is a pattern. There is NO minimum-case rule - a pattern can be emitted from a single document if the document itself names the recurring shape (e.g. an analytical book chapter that observes "X has happened repeatedly"). What makes something a pattern rather than an event: the focus is on the recurring shape itself, abstracted from any one instance. Pattern node names should describe the shape in a sentence-like form ("UAP observed in proximity to nuclear facilities") not a single case ("the Malmstrom incident").

WHERE THE OLD "matter" TYPE GOES - if you would previously have emitted a node of type matter, classify it instead as:
  - bounded-time activity (e.g. "the Nimitz operation 2004-11-10 to 2004-11-16") -> EVENT with metadata.date_end
  - ongoing operational structure (e.g. "AATIP programme", "Project Blue Book") -> ORGANISATION with metadata.kind = programme / investigation / etc
  - recognised research subject or theory (e.g. "US anti-gravity research", "Pais Effect") -> CONCEPT
  - cross-case recurring phenomenon (e.g. "shifting official accounts", "UAP near nuclear facilities") -> PATTERN
  - If none of the above fit, the candidate is probably not a node at all - it may be a claim about an existing node, or context that does not need its own node.

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

   Specifically: do NOT bundle "who this person is" with "what they did". If a sentence introduces a person's affiliation/role AND describes an action they took, that is TWO claims. The introduction is its own administrative claim about the person's role; the action is a separate claim with its own claim_type and attestation.

   Bad (compound - introduces Knapp AND describes his action in one claim):
   "George Knapp, a journalist from KLAS-TV in Las Vegas, Nevada, published a new version of a previously leaked Harry Reid memorandum the week following The Intercept article, revealing that the names of Luis Elizondo and Harold Puthoff had previously been redacted."

   Good (split into atomic claims):
   - Claim 1 (administrative, infrastructure pass): "George Knapp is a journalist at KLAS-TV in Las Vegas, Nevada."
   - Claim 2 (administrative/hearsay, domain pass): "In June 2019, George Knapp published a new version of the Harry Reid 2009 SAP Memo, revealing that the names of Luis Elizondo and Harold Puthoff had previously been redacted."

   Same rule applies to "X, who is the Y of Z, did A" - split into "X is the Y of Z" + "X did A". And to "X arrived at place B, where they met Y" - split into "X arrived at place B" + "X met Y at place B". One verb, one assertion per claim.
2. Every claim needs a claim_type and attestation level.
3. node_references in claims should list the names of nodes the claim mentions.
4. PORTABILITY (the card test): every node name must be identifiable on its own, out of context. If you wrote the name on a card and handed it to a stranger who had never seen this document, they should be able to tell what it refers to. Names that fail the card test: "the hearing", "the testimony", "this document", "the meeting", "the briefing", "the report" - these only make sense in the surrounding text. ALWAYS include enough specificity (date, parties, subject) that the name stands alone. "Luis Elizondo's written testimony to the House Oversight Subcommittee on UAP, 13 November 2024" beats "the testimony". "House Oversight Subcommittee UAP hearing of 26 July 2023" beats "the hearing". "2024 AARO Historical Record Report" beats "the report". This rule applies to ALL node types - persons, organisations, places, events, objects, documents, concepts, patterns.

4a. REDACTED AND ANONYMOUS PEOPLE - DO NOT EXTRACT AS PERSON NODES. If the source identifies an actor only by job title plus "(redacted)" or "(name redacted)" or similar, that actor has NO extractable identity and MUST NOT become a person node. The Nimitz Carrier Strike Group AAV Incident Report is the canonical case: "the USS Louisville Submarine Officer (redacted) reported X" must NOT produce a person node named "USS Louisville Submarine Officer (redacted)". Instead, attribute the claim to USS Louisville (the ship - emit as an object node or organisation as appropriate) and describe the role in the claim text: "a USS Louisville submarine officer reported X". Same for "3rd Fleet N2 (redacted)" - attribute to "3rd Fleet Intelligence" as an organisation, role in text. The presence of "(redacted)" or "(name redacted)" in a candidate person name is an absolute disqualification. Do NOT create such nodes even if it makes attribution harder.

4b. ACRONYM EXPANSION IN NODE NAMES: write acronyms as "Full Name (ACRONYM)". This is MANDATORY for every domain-specific abbreviation. NEVER emit a bare acronym as a node name when you can expand it.

   Navy squadron and unit designators (the leading letters identify the type):
   - VFA-N -> "Strike Fighter Squadron N (VFA-N)" (e.g. VFA-41 -> "Strike Fighter Squadron 41 (VFA-41)")
   - VMFA-N -> "Marine Fighter Attack Squadron N (VMFA-N)"
   - VAQ-N -> "Electronic Attack Squadron N (VAQ-N)"
   - VAW-N -> "Carrier Airborne Early Warning Squadron N (VAW-N)"
   - HS-N -> "Helicopter Anti-Submarine Squadron N (HS-N)"
   - VRC-N -> "Fleet Logistics Support Squadron N (VRC-N)"
   - CSG-N -> "Carrier Strike Group N (CSG-N)" (CSG-11 -> "Carrier Strike Group 11 (CSG-11)")
   - CVW-N -> "Carrier Air Wing N (CVW-N)"

   Programmes and agencies: AATIP -> "Advanced Aerospace Threat Identification Program (AATIP)"; AAWSAP -> "Advanced Aerospace Weapon System Applications Program (AAWSAP)"; AARO -> "All-Domain Anomaly Resolution Office (AARO)"; DIA -> "Defense Intelligence Agency (DIA)"; DARPA -> "Defense Advanced Research Projects Agency (DARPA)"; OSD -> "Office of the Secretary of Defense (OSD)"; CVIC -> "Carrier Intelligence Center (CVIC)".

   Domain abbreviations: UAP -> "Unidentified Anomalous Phenomena (UAP)" only when the node is the concept itself; AAV -> "Anomalous Aerial Vehicle (AAV)"; FLIR -> "forward-looking infrared (FLIR)"; WSO -> "weapons systems officer (WSO)"; SCIF -> "Sensitive Compartmented Information Facility (SCIF)".

   Aircraft type designators are proper names and DO NOT need expansion: "F/A-18F Super Hornet" (not "Fighter Attack 18F"), "E-2C Hawkeye", "AV-8B Harrier".

   Critically: NEVER put the node type in parens. Wrong: "USS Princeton Senior Master of Arms (person)", "AARO HR2 Volume I (document)", "AAV (object)". The parens are RESERVED for the acronym only.

   WITHIN-DIGEST DEDUPLICATION (MANDATORY PRE-EMIT PROCEDURE): each real-world entity is ONE node. Multiple nodes for the same entity is the single largest source of graph noise.

   PROCEDURE - run this against your nodes list BEFORE emitting it, every time:

   1. Sort your candidate nodes alphabetically by name.
   2. Walk the sorted list. For each adjacent pair, ask the IDENTITY TEST: "If I told a knowledgeable reader of this document that node A and node B refer to the same body / event / place / object, would they agree?" If yes, MERGE.
   3. After adjacent merging, do a second pass on the alphabetised list. Anywhere you see acronyms or country-prefixes, look at OTHER positions in the list for a paired form. Examples:
      - "DoD", "US DoD", "Department of Defense (DoD)", "United States Department of Defense" - ONE node, pick the longest expanded form.
      - "Navy", "US Navy", "United States Navy" - ONE node, pick "United States Navy".
      - "AATIP", "Advanced Aerospace Threat Identification Program (AATIP)" - ONE node, pick the expanded form.
   4. For events about the same occurrence at different date granularities ("DoD UAP Video Release, 2020" + "DoD UAP Video Release, 2020-04-27"), ONE node, pick the more precise date.
   5. After merging, every node_references field across every claim must use the SURVIVING canonical name. Rewrite references that pointed to a now-merged variant.

   ENFORCEMENT: if your final nodes list contains TWO entries whose names differ only by (a) acronym present/absent, (b) acronym expanded/bare inside the name, (c) country prefix present/absent or US vs United States, (d) date precision, or (e) word ordering, that is a BUG. Re-merge before emitting.

   Worked example - candidate list with duplicates:
     - Department of Defense (DoD)            <- (a) and (c) variants of:
     - United States Department of Defense    <-
     - US Navy                                 <- (c) variant of:
     - United States Navy                      <-
     - DoD UAP Video Release, 2020             <- (d) variant of:
     - DoD UAP Video Release, 2020-04-27       <-

   After mandatory dedup:
     - United States Department of Defense (DoD)    <- one canonical form
     - United States Navy                            <- one canonical form
     - DoD UAP Video Release, 2020-04-27             <- more precise date wins

4c. ACRONYM EXPANSION IN CLAIM TEXT: inside the prose of claim "content", expand each acronym on its first appearance with the acronym in parentheses: "forward-looking infrared (FLIR) pod", "Anomalous Aerial Vehicle (AAV)", "weapons systems officer (WSO)". Subsequent uses within the same claim may use the acronym alone. The original_excerpt preserves the source's exact wording (which may use the acronym alone); the normalised content always introduces acronyms in full. A reader of one claim in isolation must not have to look up a domain-specific abbreviation.

   EXCEPTION - the SAFE ACRONYMS list. Some acronyms are universally recognisable to any educated reader and do NOT need expansion: UFO, UAP, CIA, FBI, NSA, NASA, DOD, DoD, FAA, NATO, UN, EU, US, USA, UK, USSR, GPS, TV, CPU, GPU, USB, URL, API. These are used BARE in claim text and node names - never write "Unidentified Flying Object (UFO)", just "UFO". Never write "Central Intelligence Agency (CIA)", just "CIA". The same applies to node names: an organisation node named "Central Intelligence Agency" stays as "Central Intelligence Agency" without appending "(CIA)" since the agency is recognised by either name. If a name is JUST the acronym (e.g. "CIA"), leave it bare.
5. PERSON CLASSIFICATION: the road-trip test. A person node is a single, named, individual HUMAN BEING. Before classifying something as a person, ask: "could a human plausibly go on a road trip with this entity? Share a meal with them? Meet them at a coffee shop?" If no, it is NOT a person. A school, agency, programme, ship, squadron, or office is not a person regardless of how its name happens to be phrased. Names ending in "School", "University", "College", "Agency", "Department", "Bureau", "Office", "Centre", "Center", "Institute", "Service", "Foundation", "Corporation", "Inc", "Ltd", "LLC", "Group", "Programme", "Program", "Squadron", "Fleet", "Wing", "Command" are ALWAYS organisations, never persons. "Harvard Medical School" is an organisation, never a person; the Last-First normaliser must not fire on it.

   PERSON NAME FORMAT (once you have confirmed it IS a person): use "Last, First Middle" order with NO titles, ranks, honourifics, or suffixes (decision 0023 + decision 0026). "Commander David Fravor, US Navy (Ret.)" -> "Fravor, David". "Dr Salvatore Pais" -> "Pais, Salvatore". "Senator Marco Rubio" -> "Rubio, Marco". Single-name historical figures or pseudonyms stay as-is ("Madonna", "Whiskey-99"). If only a surname plus a rank is known ("Lieutenant Commander Moya"), use just the surname ("Moya"). Informal/short forms ("Lue Elizondo", "Dave Grusch") are aliases, not the canonical name. Do NOT encode relationships in person names; "Janise Elizondo (mother)" should be "Elizondo, Janise" with the relationship in claim text.
6. PERSON REFERENCES INSIDE CLAIM TEXT use the full natural-order name ("Luis Elizondo", "David Fravor"), NOT a surname-only shortcut. Each claim is read on its own in the graph, so "Elizondo asserts X" or "Fravor describes Y" is ambiguous out of context. Write "Luis Elizondo asserts X" and "David Fravor describes Y". The node_references field still uses the canonical "Last, First" form - the rule applies only to prose inside claim "content" and "speaker" labels in narration. Single-name historical figures or pseudonyms are an exception.

6a. ANCHORING - USE AN ANCHOR ONLY WHEN THE CLAIM IS ABOUT THE MAIN MATTER OR EVENT.

   The metadata associated with every claim already names the source document (its title, date, and id). Repeating the document name as a prefix on every claim text is NOT real portability - it duplicates information the metadata carries and the workbench already displays as the record header. Real portability means the claim text is intelligible standing alone WHEN MIXED WITH CLAIMS FROM OTHER DOCUMENTS.

   TERMINOLOGY NOTE: in this section "main matter" means the principal SUBJECT of the document - the field is still called `main_matter` for code stability, but the value's `type` is now one of `event`, `organisation`, or `pattern` (since "matter" is no longer a valid node type per the taxonomy above).

   STEP 1 - identify the main matter and any main event the document covers (see Step 1/2 conventions below).

   STEP 2 - for EACH claim, decide: is this claim specifically about the main matter (or main event), OR is it about something else (a person's background, a peripheral fact, a quoted statement someone made, an unrelated topic the document touches on)?

   - IF the claim is about the main matter/event: anchor it with the canonical name, verbatim. Example for the Nimitz incident report (which IS centrally about the Nimitz incident, so most claims are):
     "During the Nimitz UAP Incident, 2004, Chad Underwood's F/A-18F radar showed initial Anomalous Aerial Vehicle (AAV) tracks at approximately 56 to 74 kilometres south of the aircraft."

   - IF the claim is NOT about the main matter/event: write it naturally with no forced anchor prefix. The metadata gives the source. Example from a Grusch news article (which is a journalism piece reporting various statements):
     Bad (redundant document-name prefix - the metadata already says where this came from):
     "Grusch UAP Whistleblower Disclosure, 2023: Jonathan Gray stated, 'non-human intelligence phenomenon is real.'"
     Good (no prefix - the metadata names the source; the claim reads naturally):
     "Jonathan Gray stated, 'non-human intelligence phenomenon is real.'"

   The test: if the document IS the event being described (incident report, formal testimony, official statement, single-event interview), most claims anchor to the main matter. If the document is JOURNALISM, a book, a podcast, or a feature ranging over multiple topics, few or no claims need a forced anchor - write each one naturally.

   FORBIDDEN STILL: when you DO use an anchor, only main_matter.name and main_event.name are valid. Never invent your own sub-event ("Underwood Flight FLIR Contact 2004-11-14" is forbidden as an anchor). Sub-narratives go in the claim body as noun phrases.

   STEP 3 conventions for the main matter / main event names (when you emit them as nodes):
   - The universally-known identifier (Nimitz Carrier Strike Group, AATIP, House Oversight Subcommittee) - NOT internal codenames or callsigns
   - The subject in full, expanding any acronym inline
   - ISO date format (YYYY or YYYY-MM or YYYY-MM-DD or YYYY-MM-DD to YYYY-MM-DD)
   - Wikipedia-article-title register: short, recognisable, max ~8 words

   The sub-narrative ("Chad Underwood's F/A-18F FLIR contact") becomes part of the prose. The anchor stays the main_matter. Do this for EVERY claim about Underwood, every claim about the FASTEAGLE flight, every claim about the CVIC debrief, every claim about any sub-narrative. The anchor is always main_matter or main_event - nothing else.

   STEP 4 - every claim text begins with (or otherwise explicitly embeds) the verbatim canonical name of its anchor. Verbatim. No paraphrasing.

   The anchor positions the claim in CONTEXT (the time, place, or framing event), not as quoted speech. Use it as a temporal / contextual scene-setter. The form is "During X, [claim]" or "In X, [claim]" or "At X, [claim]" - not "X says that [claim]" or "X states that [claim]" or "X testified that [claim]".

   This matters: the document's metadata already names the source. Writing "The DoD Navy UAP Videos Release Statement, 2020-04-27 states that..." in front of every claim turns each claim into a quotation OF the document, which duplicates the metadata's job and makes every claim sound like reported speech. The claim should BE the assertion, with the anchor positioning it in time/place.

   STRICTLY FORBIDDEN ANCHOR VERBS at the head of a claim:
   - "X states that..."
   - "X says that..."
   - "X said that..."
   - "X declared that..."
   - "X announced that..."
   - "X testified that..."
   - "X reported that..."

   These verbal forms turn the anchor into a hedge ("according to X, ..."), which is precisely what the metadata already does. Replace with a positional preposition.

   Concrete corrections:

   Bad (over-anchored, every claim quotes the document):
     "The DoD Navy UAP Videos Release Statement, 2020-04-27 states that the United States Department of Defense authorised the release of three unclassified Navy videos."
     "The DoD Navy UAP Videos Release Statement, 2020-04-27 states that one of the three Navy videos was taken in 2004-11."

   Good (anchor positions; claim asserts):
     "In the DoD Navy UAP Videos Release Statement, 2020-04-27, the United States Department of Defense authorised the release of three unclassified Navy videos."
     "Of the three Navy videos released on 2020-04-27, one was filmed in 2004-11."

   Bad (verbal "testified that" wraps every claim about the testimony):
     "During the Elizondo House UAP Testimony, 2024-11-13, Luis Elizondo testified that UAP are real."

   Good (the speaker is named once, the rest is direct assertion):
     "In his House Oversight Subcommittee testimony on 2024-11-13, Luis Elizondo asserted that UAP are real."
     - or, if the speaker is implicit from the document: "UAP are real."
     - the rule of thumb: if the document IS the speaker's testimony, you do not need to say "the speaker testified" for every claim. That fact is the metadata. State the assertion.

   Reported quotations are different from anchored claims. When the source contains a literal direct quote that you want to preserve verbatim, the form is "X said: 'quote'" inside the body of the claim, and the anchor (if any) is the surrounding event. The "X said" form is permitted INSIDE a claim for verbatim quotation; it is not permitted as the ANCHOR PREFIX of every claim about the document.

   STEP 5 - WITHIN ONE CLAIM, expand each acronym on FIRST USE ONLY. Subsequent uses in the same claim stay BARE (or use "the" + bare). Do NOT write "Anomalous Aerial Vehicle (AAV)" twice in one claim - that is a bug. After "Anomalous Aerial Vehicle (AAV)" appears once, every later mention in that claim is just "AAV" or "the AAV".

   FORBIDDEN IN ANCHORS:
   - Codenames or callsigns (FASTEAGLE, Tic Tac, Fast Walker) - these are operational shorthand, meaningless to outsiders, never portable. Codenames may appear inside the BODY of a claim once the subject is identified, but never as part of the anchor that makes the claim portable.
   - Bare acronyms (AAV, FLIR, NDA, WSO) - all must be expanded inline within the anchor itself ("Anomalous Aerial Vehicle (AAV)") because the anchor IS the first use within each claim.
   - Spelled-out months ("November 2004") - always ISO ("2004-11").
   - Vague references ("the incident", "the encounter") that depend on knowing which document the claim came from.

   Bad anchor (uses codename, unexpanded acronym, spelled month, missing identifier):
   "During the FASTEAGLE Flight AAV Intercept 14 November 2004, Chad Underwood was not asked to sign any NDA."

   Good anchor (universally-known identifier, expanded acronym, ISO date, broad matter):
   "During the Nimitz Carrier Strike Group Anomalous Aerial Vehicle (AAV) Detection Matter (2004-11-10 to 2004-11-16), F/A-18F pilot Chad Underwood was not asked to sign any non-disclosure agreement (NDA) and was uncertain how far up the chain the reporting went past his commanding officer."

   Yes the anchor is long. Long is fine - it carries the whole context. Long is the cost of portability. Do not shorten it.

6b. ISO DATE FORMAT MANDATORY EVERYWHERE - claim text AND node names. NO spelled-out months ANYWHERE in the normalised output. "2004-11-14" not "14 November 2004"; "2004-11" not "November 2004"; "2024-08-19" not "19 August 2024"; "1947" not "the year 1947". Event node names use ISO dates: "Nimitz F/A-18F Intercept of Anomalous Aerial Vehicle, 2004-11-14", not "...14 November 2004". The original_excerpt preserves source phrasing (which often uses English month names); the normalised content and all node names are always ISO. If you find yourself writing a month name in prose, STOP and convert.

6c. CODENAMES ARE NOT NODES. Callsigns (FASTEAGLE 01, FASTEAGLE 02), military codenames (Tic Tac, Fast Walker), and internal nicknames are operational shorthand that resolves to one or more actual persons/objects on a specific mission. Do not emit a codename as a person, object, event, or matter node. When the source uses a codename, in claim text refer to the underlying entity by its real identifier ("F/A-18F flown by David Fravor" or simply "David Fravor's flight") and mention the codename in passing if useful ("...callsign FASTEAGLE 01"). The codename is never the canonical identifier.
7. PLACES vs ORGANISATIONS - the building vs institution distinction. A place is somewhere a human can physically go and find. A named building is a PLACE: the Pentagon, the Capitol Building, the White House, Buckingham Palace. The institution housed inside a building is a different node - an ORGANISATION. The Pentagon (the building, in Arlington, Virginia) is a place; the Department of Defense (the institution that occupies it) is an organisation. When the source says "the Pentagon announced X", that is colloquial reference to the Department of Defense (organisation), not the building - attribute the claim to the Department of Defense, and only emit a "Pentagon" place node if the source talks about the building itself (people going there, events happening there).

   A place is named, specific, and durable enough that someone could go to it and still find it decades later: a military base, installation, research facility, ranch, named site, airport, town or city, named building. Do NOT extract as places: countries ("USA"), states or provinces ("Maryland"), oceans or operating areas ("the Nimitz operating area"), or broad regions - you cannot meet someone "in America". When the text discusses a named installation named after a geographic feature ("Naval Air Warfare Center on the Patuxent River"), the PLACE is the installation ("Naval Air Station Patuxent River"), NOT the feature.

   Name qualifying places "Country, Region, Specific" largest-unit-first ("USA, Nevada, Area 51", "Australia, Queensland, Tully", "USA, New Mexico, Roswell"). The country/state prefix is for disambiguation and sorting; it does NOT mean the country or state is itself a separate place node.

7a. PROGRAMME / INVESTIGATION as ORGANISATION SUB-KIND: a named programme or investigation is an ORGANISATION with optional `metadata.kind` set to `programme` or `investigation`. "Advanced Aerospace Threat Identification Program (AATIP)" = organisation, `kind: programme`. "Project Blue Book" = organisation, `kind: investigation`. Do NOT emit a separate event-or-other-typed node for the work the programme performs over time - that work is described in claims attached to the organisation. If you ALSO want to refer to the temporal period during which the programme operated as a distinct subject (e.g. "the 2007-2012 AATIP era" treated as a thing that happened), emit an event with date_start/date_end alongside the organisation. The DEFAULT for a named programme/investigation is: emit only the organisation; only add an event-as-period if the document treats the operational period itself as a discrete subject.

8. Events MUST have at least a year for date_start. If a candidate "event" has no date at all, it is not actually an event - reconsider what type it is. Bounded events use both date_start and date_end (e.g. Nimitz UAP encounter date_start: 2004-11-10, date_end: 2004-11-16; Watergate investigation date_start: 1972, date_end: 1974).
9. Normalise all text to English regardless of source language.
10. speaker is the person making the assertion (may differ from the document's author).
11. location_in_record is where in the document the claim appears (page, timestamp, paragraph).
12. UNIT NORMALISATION: Convert all measurements in "content" to metric units using full unit names. Write "10 metres" not "10m", "24,000 metres" not "24km", "1,200 kilometres per hour" not "1200 km/h". Use the exact format: number + space + full unit name (metres, kilometres, kilograms, degrees Celsius, etc.). IMPORTANT: Preserve the original level of precision. If the source says "about 80,000 feet", convert to "approximately 24,000 metres" (rounded), NOT "24,384 metres" (over-precise). Round to the same number of significant figures as the original.
13. original_excerpt: Preserve the EXACT original wording from the source document, including original units, language, and phrasing. This is for attribution and provenance. If the source says "about 30 to 40 feet", the original_excerpt must say exactly that.

================================================================
FINAL CHECK - DO THIS BEFORE EMITTING YOUR JSON RESPONSE
================================================================

Sort your nodes list alphabetically by name. Walk it. For every pair where
the two names differ only by ONE OR MORE of the following, MERGE them into
ONE node (pick the longer / more-precise canonical form, drop the other,
and replace all references to the dropped form in claim node_references):

  - acronym suffix present vs absent ("DoD" vs "Department of Defense (DoD)")
  - country / nation prefix present vs absent in any form ("Navy" vs "US Navy" vs "United States Navy")
  - acronym expanded mid-name vs not ("IC Inspector General" vs "Intelligence Community (IC) Inspector General")
  - same event, different date precision ("Release, 2020" vs "Release, 2020-04-27")
  - same body, same words, different ordering or rephrasing

If your nodes list still contains two entries that refer to the same
real-world entity after that pass, the output is wrong. Re-merge before
emitting. This check is not optional.

OUTPUT FORMAT (respond with ONLY valid JSON, no markdown fencing):

{{"record_title": "short title for this document",
"record_date": "YYYY-MM-DD or YYYY-MM or YYYY if known",
"record_producer": "person or organisation that produced this document",
"nodes": [
    {{"name": "canonical short name", "node_type": "person|organisation|place|event|object|document|concept|pattern", "metadata": {{"date_start": "...", "date_end": "...", "kind": "programme|investigation|agency|... (organisations only, optional)"}}}}
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
    t.value
    for t in NodeType
    if t not in (NodeType.record, NodeType.claim, NodeType.matter)
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
            "extraction_complete": {"type": "boolean"},
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
- "organisation": a named entity distinct from any single person (includes podcasts, news outlets, publications, agencies, companies; programmes and investigations are organisations with optional metadata.kind)
- "place": a named geographic location
- "event": a discrete or bounded-in-time thing that happened (must have at least a start year; can also have date_end for periods)
- "object": a specific named physical thing
- "document": a specific piece of content (a book, a podcast episode, a documentary, an article, a memo)
- "concept": a recognised named idea or principle
- "pattern": a recurring shape observed across cases

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

NAMING RULES (apply to every node and every claim text):
- PORTABILITY (the card test): every node name must be identifiable on its own, out of context. If you wrote the name on a card and handed it to a stranger who had never seen this document, they should tell what it refers to. Names that fail the card test: "the hearing", "the testimony", "this document", "the interview", "the meeting". ALWAYS include enough specificity (date, parties, subject) that the name stands alone.
- REDACTED OR ANONYMOUS PEOPLE: do not extract a person node when the source identifies the actor only by role with a redacted/unknown name ("USS Louisville Commander (redacted)", "3rd Fleet N2 (redacted)"). There is no person to extract. Attribute the claim to the relevant organisation and describe the role in claim text.
- ACRONYM EXPANSION IN NODE NAMES: write acronyms in node names as "Full Name (ACRONYM)". "VFA-41" -> "Strike Fighter Squadron 41 (VFA-41)". "CSG-11" -> "Carrier Strike Group 11 (CSG-11)". Applies to organisations, documents, events, objects, concepts.
- ACRONYM EXPANSION IN CLAIM TEXT: inside claim "content", expand acronyms on first use with the acronym in parentheses ("Anomalous Aerial Vehicle (AAV)", "forward-looking infrared (FLIR)"). The original_excerpt preserves the source's exact phrasing.
- SAFE ACRONYMS - do NOT expand: UFO, UAP, CIA, FBI, NSA, NASA, DOD, DoD, FAA, NATO, UN, EU, US, USA, UK, USSR, GPS, TV, CPU, GPU, USB, URL, API. These are universally known; use them bare in claim text and node names.
- Person nodes use "Last, First Middle" order with NO titles/ranks/honourifics (decision 0023 + 0026). "Commander David Fravor" becomes "Fravor, David".
- Inside the prose of claim "content" and "speaker" narration, refer to people by their full natural-order name ("Luis Elizondo", not "Elizondo") so each claim is intelligible in isolation. The node_references field still uses the canonical "Last, First" form.

OUTPUT FORMAT (respond with ONLY valid JSON, no markdown fencing):

{{"record_title": "short title for this document",
"record_date": "YYYY-MM-DD or YYYY-MM or YYYY if known",
"record_producer": "person or organisation that produced this document",
"nodes": [
    {{"name": "canonical short name", "node_type": "person|organisation|place|event|object|document|concept|pattern", "metadata": {{"sentiment": "positive|negative|neutral"}}}}
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


# Acronyms universally recognisable to any educated reader. The model is told
# not to expand these in claim text or node names, and the terminology pre-pass
# is told not to include them in the document's acronym map. Keep this list
# tight - bias toward expanding anything domain-specific (AATIP, AAWSAP, FLIR
# etc.) and only mark something safe when a stranger reading a single claim
# would clearly know what it means.
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
    record_context: str = "",
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
        prompt = (record_context + base_prompt) if record_context else base_prompt
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
                + "\n\nExtract ADDITIONAL factual claims from the document that"
                " are NOT in the list above. If the only claims remaining would"
                " be trivial, redundant, or low-confidence, do NOT pad - return"
                " an empty `claims` array and set `extraction_complete` to true."
                " Stopping cleanly is better than producing marginal claims."
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
            done_flag = " [model: complete]" if result.extraction_complete else ""
            on_progress(
                f"    iter {iteration + 1}: +{new_in_round} claims "
                f"(chunk total {len(chunk_claims)}){done_flag}"
            )

        # Primary stop: the model says it is done. Backstop: the count floor
        # (only fires if the model never admits completion). The ITERATION_MAX
        # range() is the runaway ceiling.
        if result.extraction_complete:
            break
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
    record_context: str = "",
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
            record_context=record_context,
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


def build_record_context(
    title: str,
    authors: list[str] | None,
    date: str | None,
    source_type: str | None,
) -> str:
    """Build the SOURCE RECORD framing prepended to every extraction prompt.

    Pins first-person / "the author" references to the named author so the
    model never emits an unpinned "the author" node. Unpinned nodes are
    graph-wide contaminants because nodes are global (shared across records),
    so a vague node would wrongly merge across every first-person source.
    """
    author_str = ", ".join(authors) if authors else None
    bits = [f'"{title}"' if title else "an untitled record"]
    if source_type:
        bits.append(f"({source_type})")
    if author_str:
        bits.append(f"by {author_str}")
    if date:
        bits.append(f"dated {date}")
    line = "SOURCE RECORD: " + " ".join(bits) + ".\n"
    if author_str:
        line += (
            f'When the text uses "the author", "I", "me", "my", or first '
            f"person, that refers to {author_str}. Resolve such references to "
            f'the named person; never emit "the author" or a vague '
            f"first-person entity as a node.\n"
        )
    return line + "\n"


def extract(
    text: str,
    model: str = DEFAULT_MODEL,
    use_api: bool = False,
    existing_nodes: list[tuple[str, str]] | None = None,
    record_context: str = "",
    on_progress=None,
) -> ExtractionResult:
    """Extract nodes and claims from record text.

    Args:
        existing_nodes: list of (name, node_type) tuples for the node directory.
        record_context: SOURCE RECORD framing (see build_record_context).
        on_progress: optional callback receiving status strings (for chunked runs).
    """
    return _extract_chunked(
        text=text,
        base_prompt=EXTRACTION_PROMPT,
        schema=DOMAIN_SCHEMA,
        model=model,
        use_api=use_api,
        existing_nodes=existing_nodes,
        record_context=record_context,
        on_progress=on_progress,
    )


def extract_infrastructure(
    text: str,
    model: str = DEFAULT_MODEL,
    use_api: bool = False,
    existing_nodes: list[tuple[str, str]] | None = None,
    record_context: str = "",
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
        record_context=record_context,
        on_progress=on_progress,
    )


# ============================================================================
# TWO-PASS ARCHITECTURE (2026-05-25 onward)
#
# Replaces the old terminology-then-extract-then-infrastructure flow. The
# two-pass split solved the within-output node-duplication problem (single
# inference can't reliably dedup ~200-item output) by giving each pass a
# tighter focus and smaller output:
#
#   Pass A (nodes only): identify every named entity with canonical form.
#       Also returns main_subject + codenames_to_resolve + acronym glossary
#       (folded in from the deprecated terminology pre-pass). Iterates per
#       chunk until convergence; threads the running directory across chunks.
#
#   Pass B (claims only): extract claims, constrained to using only Pass A's
#       node names in node_references (enforced by JSON schema enum on the
#       items of node_references). Each claim carries category: domain |
#       infrastructure - infrastructure claims are the source-graph cross-
#       references (X cites Y, X interviews Z, X recommends Y) that the
#       public site filters out but we retain for content discovery.
#
# Node taxonomy is the 8-type set: person, organisation, project, place,
# event (with optional date_end), object, document, topic. The old
# matter / programme / investigation / pattern / concept / principle types
# stay in the NodeType enum for back-compat but are not in the extraction enum.
# ============================================================================

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


NODES_PROMPT_V2 = """You are extracting the COMPLETE NODE DIRECTORY for a knowledge graph from a single document chunk.

Your ONLY job in this call is to identify every distinct named entity in the chunk and return ONE canonical record per real-world thing. Claims are a separate pass.

================================================================
NODE TYPES (eight - choose one per node)
================================================================

- "person": a named human individual. Format "Last, First Middle". No titles/ranks/honourifics. Pseudonyms and single-name historical figures stay as-is. Do NOT create person nodes for redacted/anonymous actors ("USS Louisville Officer (redacted)") - attribute to the relevant organisation instead.

- "organisation": a named acting BODY - government agencies, military units, companies, research institutes, publications, news outlets, committees, standing offices, foundations. Distinguished from project: an organisation is the BODY; a project is the WORK it runs.

- "project": a NAMED time-bounded or initiative-bounded effort - programmes, investigations, operations, research projects, official inquiries. AATIP, Project Apollo, Project Blue Book, AAWSAP, the Condon Committee inquiry, the AARO Historical Record review, the Manhattan Project, OXCART, Stargate. The US Air Force is an organisation; Project Blue Book is a project the Air Force ran.

- "place": a named geographic location. Format "Country, Region, Specific" largest-unit-first. "USA, Nevada, Area 51". Do NOT extract countries/states/regions as places.

- "event": a discrete or bounded-in-time occurrence. Has at least a start year. Can span hours, days, months, years - use metadata.date_start (required) and optionally metadata.date_end. The Nimitz UAP encounter 2004-11-10 to 2004-11-16 is ONE event.

- "object": a specific named PHYSICAL thing - craft, vessel, vehicle, sample, device, named building, recovered material. Must pass the touch test - you could imagine reaching out and touching it. Phenomena, effects, video footage all FAIL the touch test.

- "document": a written or recorded artefact - book, report, paper, FOIA release, video footage, podcast episode, article, memo, testimony, affidavit, patent application.

- "topic": a RECOGNISED named idea, theory, framework, or phenomenon that exists independent of this document (general relativity, the Pais Effect, anti-gravity propulsion, zero-point energy, vacuum polarisation). NOT a specific named alleged craft (TR-3B is NOT a topic - it is an alleged craft, classify as object or document). NOT generic touchable nouns (gravity, plasma). NOT mechanisms lifted from patent jargon. NOT vague catch-alls. NOT ad-hoc theories named only within this document.

NOTE: there is no "matter", "concept", or "pattern" type for extraction in this pass. Things that previously would have been matters now classify as event (bounded time), organisation (standing body), project (named effort), or topic (recognised idea). Cross-case patterns are curator-created, not extractor-emitted.

================================================================
PORTABILITY - the card test
================================================================

Every node name must be identifiable on its own, out of context. "the testimony", "the hearing", "the report" all FAIL - include enough specificity (date, parties, subject) that the name stands alone.

================================================================
ACRONYM EXPANSION in node names
================================================================

Write acronyms as "Full Name (ACRONYM)":
  - AATIP -> "Advanced Aerospace Threat Identification Program (AATIP)"
  - AARO -> "All-Domain Anomaly Resolution Office (AARO)"
  - DIA -> "Defense Intelligence Agency (DIA)"
  - VFA-41 -> "Strike Fighter Squadron 41 (VFA-41)"
  - CSG-11 -> "Carrier Strike Group 11 (CSG-11)"
  - NAVAIR -> "Naval Air Systems Command (NAVAIR)"
  - LIGO -> "Laser Interferometer Gravitational-Wave Observatory (LIGO)"

SAFE ACRONYMS (use bare, never expand): UFO, UAP, CIA, FBI, NSA, NASA, DoD, FAA, NATO, UN, EU, US, USA, UK, USSR, GPS, TV, CPU, GPU, USB, URL, API.

================================================================
DEDUPLICATION - mandatory
================================================================

Each real-world entity appears as ONE node only. Before emitting, check for these duplicate variants and merge:

  (a) Acronym suffix present vs absent: "Defense Intelligence Agency" + "Defense Intelligence Agency (DIA)" - emit ONCE with acronym.
  (b) Country prefix variants: "Navy" + "US Navy" + "United States Navy" - emit ONCE.
  (c) DoD variants: "DoD" + "Department of Defense" + "United States Department of Defense" + "Department of Defense (DoD)" - emit ONCE.
  (d) US/UK spelling: "Naval Air Warfare Center" + "Naval Air Warfare Centre" - emit ONCE.
  (e) Long-form vs short-form: "House Oversight Subcommittee" + "the subcommittee" - emit ONCE with the long portable form.
  (f) Descriptor parentheses: "Project Unity" + "Project Unity (podcast)" - emit ONCE.

If a candidate appears in the EXISTING NODE DIRECTORY below (from prior chunks), use the EXACT name from the directory - do NOT create a variant.

================================================================
ALSO RETURN with the nodes list
================================================================

  - main_subject: the canonical NAME of the one node from your list (or the existing directory) that is this document's principal subject. Used by downstream claim extraction to anchor claims. If this is a chunk of a larger document, the main subject is the same across all chunks; just emit it.

  - codenames_to_resolve: callsigns and military codenames (FASTEAGLE 01, Tic Tac, Fast Walker) that should NEVER become person/object nodes. For each, name the real entity it refers to.

  - acronyms: every domain-specific acronym, with its expansion. Helps the claims pass expand on first use.

================================================================
OUTPUT FORMAT - valid JSON only, no markdown fencing
================================================================

{{
  "main_subject": "canonical name",
  "nodes": [
    {{
      "name": "canonical portable name",
      "node_type": "person|organisation|project|place|event|object|document|topic",
      "metadata": {{"date_start": "...", "date_end": "..." (events only, optional)}}
    }}
  ],
  "codenames_to_resolve": [{{"codename": "X", "refers_to": "canonical name"}}],
  "acronyms": [{{"acronym": "AAV", "expansion": "Anomalous Aerial Vehicle"}}],
  "extraction_complete": true
}}

If the model has emitted everything it can from this chunk in this round (no more nodes to add), set extraction_complete=true. The iterative loop reads this and stops asking for more.
"""


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


CLAIMS_PROMPT_V2_TEMPLATE = """You are extracting EVERY factual claim from a document chunk for a knowledge graph. A previous pass has identified all named entities (the NODE DIRECTORY below). Each claim must reference ONLY those existing nodes by their exact canonical names.

================================================================
NODE DIRECTORY - use ONLY these names in node_references
================================================================

{directory}

MAIN SUBJECT of this document: {main_subject}
  Use this name verbatim as the anchor for claims that are specifically about the main subject (see anchoring rules below).

{codenames_block}

{acronyms_block}

================================================================
CLAIM CATEGORIES - every claim is tagged with one
================================================================

- "domain" (most claims): facts about UAP, witnesses, encounters, investigations, careers, programmes, organisations, biographical facts, observations, measurements, official statements, findings. Anything that establishes a fact about the world.

- "infrastructure" (the source-graph): claims whose purpose is to point at OTHER content - X cites Y, X recommends Z, X interviewed Y, X appeared on Z's podcast, X mentions Y's book. "Coulthart cites Vallée's Passport to Magonia" = infrastructure. "Mellon appeared on Fox & Friends" = infrastructure. "Bender wrote in Politico that..." (where the substance is "X reported Y elsewhere"): infrastructure if the focus is the report, domain if the focus is the substantive fact Y. NOT publication facts about the document itself - those are still infrastructure but be sparing.

The rule of thumb: if the claim's purpose is "look at this other content" or "X is recommending/citing Y", it is infrastructure. If the claim establishes a substantive fact (even when sourced from another publication), it is domain.

================================================================
ATOMIC CLAIMS - one assertion per claim, split compound statements
================================================================

Do NOT bundle "who this person is" with "what they did". "George Knapp, a journalist at KLAS-TV, published the memo in June 2019" splits into:
  Claim 1: "George Knapp is a journalist at KLAS-TV in Las Vegas." (administrative, infrastructure if it's only about source attribution; domain if it's substantive)
  Claim 2: "In June 2019, George Knapp published the Harry Reid 2009 SAP Memo." (administrative, domain)

A sentence listing SEVERAL distinct capabilities, measurements, or properties becomes SEVERAL claims - never one merged claim. "a technology that can do 600-700 G-forces, fly at 13,000 miles per hour, evade radar, fly through air and water, with no wings or propulsion, and defy gravity" splits into SEPARATE claims: one for the 600-700 G-force figure, one for the speed, one for evading radar, one for travelling through air and water, one for the absence of wings/propulsion, one for defying gravity. Each measurement or property is its own claim. Merging a list into a single narrative sentence is WRONG - extract the individual facts.

================================================================
ANCHORING
================================================================

If a claim is specifically ABOUT the main subject, anchor with the subject's exact name as a temporal/contextual scene-setter ("During X, ..." / "In X, ..." / "At X, ..."). DO NOT use "X states that..." / "X says that..." / "X testified that..." as anchors - that turns claims into reported speech and duplicates the metadata. The claim should BE the assertion.

If the claim is NOT about the main subject, write naturally with no anchor prefix.

Forbidden anchor verbs at the head of a claim: "X states that", "X says that", "X testified that", "X declared that", "X announced that", "X reported that". Use a positional preposition or no anchor instead.

================================================================
PERSON REFERENCES IN CLAIM TEXT
================================================================

Use the full natural-order name inside claim text ("Luis Elizondo", "David Fravor") - NOT a surname-only shortcut. node_references uses the canonical "Last, First" form from the directory.

================================================================
UNIT NORMALISATION - metric only, never leave imperial
================================================================

ALWAYS convert imperial/US units in "content" to metric. NEVER leave a bare imperial value in a claim. miles per hour -> km/h, miles -> kilometres, feet -> metres, pounds -> kilograms, Fahrenheit -> Celsius. "13,000 miles per hour" becomes "approximately 21,000 km/h"; "about 80,000 feet" becomes "approximately 24,000 metres". Preserve precision and the original's hedge ("about", "approximately") - round, do NOT give false precision ("24,384 metres"). Knots (aviation/nautical standard) may be kept but add the km/h equivalent: "120 knots (approximately 220 km/h)".

original_excerpt preserves source phrasing verbatim (keep the imperial units there).

================================================================
BRITISH ENGLISH - mandatory in all claim text and node names
================================================================

Use British spelling everywhere: categorise (not categorize), organise, recognise, analyse, emphasise, prioritise, colour, behaviour, defence, offence, licence (noun), metre, centre, manoeuvre, aluminium, fibre. Never American spellings.

================================================================
DURABILITY - every claim must stand alone out of context
================================================================

Each claim is read in isolation in the knowledge graph, months later, by someone who has NOT seen this document. It must be fully self-contained. Resolve every vague or deictic reference into a concrete one:
  - not "the video footage" / "the footage" but the named item ("the 2004 USS Nimitz FLIR1 video").
  - not "the objects" / "these objects" / "the unidentified objects" but what they are and where ("the UAP observed off Virginia Beach in 2014").
  - not "the same area" / "the area" but the named place; not "the incident" / "the encounter" but the named event.
  - NEVER begin a claim with a bare "It", "They", "This", "These", "That" - name the subject.
A reader seeing only this one claim, with no surrounding text, must understand exactly what it refers to.

================================================================
FIDELITY - capture what was said, do not embellish
================================================================

State what the source actually says. Do NOT add reasoning, qualifiers, consequences, or detail the speaker did not express. "Pretty hard to spoof that" becomes "[Speaker] said the dual radar-and-infrared detection is hard to spoof" - NOT "...hard to spoof or dismiss as false contacts" (the speaker never said "dismiss as false contacts"). Resolve context (durability) without inventing content.

================================================================
ISO DATES MANDATORY EVERYWHERE
================================================================

Claim text uses ISO: "2004-11-14" not "14 November 2004". original_excerpt preserves source phrasing.

================================================================
ACRONYM EXPANSION IN CLAIM TEXT
================================================================

Expand each acronym on first use in a claim: "Anomalous Aerial Vehicle (AAV)", "forward-looking infrared (FLIR)". Subsequent uses in the same claim are bare. SAFE acronyms (UFO, UAP, CIA, DoD, etc) are always bare.

================================================================
EXHAUSTIVE EXTRACTION - do not summarise
================================================================

Capture every factual statement, however incidental - dates, names, places, quoted figures, asides, parenthetical remarks. Coverage matters more than highlighting "important" points.

OPINIONS AND INTERVIEW EXCHANGES: an opinion, assessment, or judgement - especially from an interviewee or expert - is a claim (claim_type "opinion"). When a person gives a short answer, confirmation, or reaction to a question or a stated proposition ("Pretty hard to spoof that", "Every day", "I don't see why not"), it IS a claim: expand it into a standalone assertion by resolving it against the question or statement it responds to, and attribute it to the person who answered. Do NOT drop a conversational turn just because it is brief or depends on the previous line for its meaning.

If a claim references an entity that does NOT appear in the node directory above, do NOT make up a name for it - either find it in the directory under a different surface form, OR skip that claim. Adding new node names breaks the locked-directory guarantee.

================================================================
ATTESTATION - optional, omit unless there is a clear evidential stance
================================================================

attestation records the evidential standing of a claim. It is OPTIONAL and depends on the
NATURE of the statement, not the speaker's role. Only set it when the claim is an evidential
account of something observed or done:
  - "first_hand": the speaker is stating what they personally did, witnessed, or observed (a pilot describing his own encounter; "I saw the object descend").
  - "second_hand": the speaker is relaying a specific other person's account or observation, or reporting a specific organisation's finding ("David Fravor told me the object accelerated"; "the Pentagon confirmed it cannot identify the objects").
  - "third_hand": the speaker is relaying something passed through an intermediary ("he said the rancher had heard that ...").

OMIT attestation entirely when there is no evidential stance to record - a narrator, host, or
interviewer simply conveying information, framing, or context, or a bare factual statement with
no observer attached. Do NOT default to first_hand. A missing attestation is correct and
expected for most narration. When a researcher or interviewee does inject a genuine first- or
second-hand account into otherwise neutral narration, tag that claim.

================================================================
OUTPUT FORMAT - valid JSON only, no markdown fencing
================================================================

{{
  "claims": [
    {{
      "content": "normalised assertion with metric units",
      "original_excerpt": "exact original wording from source",
      "category": "domain|infrastructure",
      "claim_type": "observation|testimony|hearsay|opinion|measurement|administrative",
      "attestation": "first_hand|second_hand|third_hand (OPTIONAL - omit for plain narration/framing)",
      "speaker": "person name from directory, or null",
      "location_in_record": "page, paragraph, or timestamp",
      "date": "YYYY-MM-DD if applicable",
      "node_references": ["Node A from directory", "Node B from directory"],
      "confidence": 1.0
    }}
  ],
  "extraction_complete": true
}}

Set extraction_complete=true when you have nothing further to extract from this chunk. The iterative loop stops asking when this is true.
"""


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
                    "required": ["content", "category", "claim_type"],
                    "properties": {
                        "content": {"type": "string"},
                        "original_excerpt": {"type": "string"},
                        "category": {"type": "string", "enum": CATEGORIES_V2},
                        "claim_type": {
                            "type": "string",
                            "enum": list(VALID_CLAIM_TYPES),
                        },
                        "attestation": {
                            "type": "string",
                            "enum": list(VALID_ATTESTATION),
                        },
                        "speaker": {"type": ["string", "null"]},
                        "location_in_record": {"type": "string"},
                        "date": {"type": "string"},
                        "node_references": {
                            "type": "array",
                            "items": (
                                {"type": "string", "enum": node_names}
                                if node_names
                                else {"type": "string"}
                            ),
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


def _nodes_prompt() -> str:
    """Nodes-pass prompt, overridable per run via DIGESTER_NODES_PROMPT_FILE
    (used for prompt tuning - lets Haiku and Sonnet carry different prompts)."""
    p = os.environ.get("DIGESTER_NODES_PROMPT_FILE")
    if p and os.path.exists(p):
        return open(p).read()
    return NODES_PROMPT_V2


def _claims_prompt_template() -> str:
    """Claims-pass prompt template, overridable via DIGESTER_CLAIMS_PROMPT_FILE.
    Must keep the {directory}/{main_subject}/{codenames_block}/{acronyms_block}
    placeholders and {{ }} for literal braces."""
    p = os.environ.get("DIGESTER_CLAIMS_PROMPT_FILE")
    if p and os.path.exists(p):
        return open(p).read()
    return CLAIMS_PROMPT_V2_TEMPLATE


def extract_nodes_v2(
    text: str,
    model: str = DEFAULT_MODEL,
    record_context: str = "",
    on_progress=None,
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
            raw = _call(prompt, chunk, model, schema=NODES_SCHEMA_V2)
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

    chunks = _build_chunks(text)
    merged_claims: list[dict] = []
    seen_content: set[str] = set()

    for ci, chunk in enumerate(chunks):
        if on_progress and len(chunks) > 1:
            on_progress(f"  claims chunk {ci + 1}/{len(chunks)} ({len(chunk):,} chars)")

        chunk_claims: list[dict] = []
        for it in range(ITERATION_MAX):
            prompt = record_context + _claims_prompt_template().format(
                directory=directory,
                main_subject=main_subject,
                codenames_block=codenames_block,
                acronyms_block=acronyms_block,
            )
            if chunk_claims:
                exclude = "\n".join(
                    f"{i + 1}. {c['content']}" for i, c in enumerate(chunk_claims)
                )
                prompt += (
                    "\n\nALREADY EXTRACTED CLAIMS - do NOT repeat any of these:\n"
                    + exclude
                    + "\n\nExtract ADDITIONAL factual claims from this chunk that are not in the list above. "
                    "If only trivial or redundant claims would remain, return an empty claims array "
                    "and set extraction_complete=true."
                )

            raw = _call(prompt, chunk, model, schema=schema)
            result = json.loads(raw) if isinstance(raw, str) else raw

            new_in_round = 0
            for c in result.get("claims", []):
                key = c["content"].strip().lower()
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
) -> dict:
    """Top-level v2 entry point. Runs nodes pass then claims pass. Returns
    a dict with keys: nodes, claims, main_subject, codenames_to_resolve,
    acronyms. cli.py consumes this and writes the YAML digest.
    """
    if on_progress:
        on_progress("Pass A: nodes (with iteration + chunking)")
    nodes_result = extract_nodes_v2(
        text, model=model, record_context=record_context, on_progress=on_progress
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
    }


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


API_MODEL_MAP = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "haiku": "claude-haiku-4-5-20251001",
}

_API_MAX_TOKENS = 16384


def _call_api(prompt: str, text: str, model: str, schema: dict | None = None) -> str:
    """Call Claude via the Anthropic Messages API.

    Structured extraction uses forced tool use: the JSON schema is the tool's
    input_schema and tool_choice pins that tool, so the model must answer with a
    tool_use block whose input conforms - including the node_references enum that
    locks claims to the Pass-A node names. This mirrors what the CLI's
    --json-schema did. Returns a JSON string for the existing _parse_json path.
    """
    import anthropic

    model_id = API_MODEL_MAP.get(model, model)
    client = anthropic.Anthropic()

    kwargs: dict = {
        "model": model_id,
        "max_tokens": _API_MAX_TOKENS,
        "system": prompt,
        "messages": [{"role": "user", "content": f"DOCUMENT:\n{text}"}],
    }
    if schema is not None:
        kwargs["tools"] = [
            {
                "name": "emit_extraction",
                "description": "Emit the structured extraction result.",
                "input_schema": schema,
            }
        ]
        kwargs["tool_choice"] = {"type": "tool", "name": "emit_extraction"}

    message = client.messages.create(**kwargs)

    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            f"API response hit max_tokens ({_API_MAX_TOKENS}); output truncated. "
            "Lower chunk size or raise _API_MAX_TOKENS."
        )

    if schema is not None:
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                return json.dumps(block.input)
        raise RuntimeError(
            f"API returned no tool_use block. stop_reason={message.stop_reason} "
            f"types={[getattr(b, 'type', None) for b in message.content]}"
        )
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError(f"API returned no text. stop_reason={message.stop_reason}")


def _call(prompt: str, text: str, model: str, schema: dict | None = None) -> str:
    """Dispatch an extraction call. Defaults to the Anthropic API; set
    DIGESTER_USE_API=0 to fall back to the Claude Code CLI transport."""
    if os.environ.get("DIGESTER_USE_API", "1") == "0":
        return _call_cli(prompt, text, model, schema=schema)
    return _call_api(prompt, text, model, schema=schema)


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
        extraction_complete=bool(data.get("extraction_complete", False)),
    )
