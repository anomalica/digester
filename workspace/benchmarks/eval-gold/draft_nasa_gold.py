#!/usr/bin/env python3
"""Draft PROVISIONAL highlight gold for the two small NASA UAP excerpts.

Model-drafted (not human-signed): these spans are what a reviewer would plausibly
highlight as worth extracting - the sighting reports and descriptions, not the
"Roger"/"Say again" procedural filler. Under the tuning-programme honesty rule
(ADR 0042) provisional gold may only SCREEN hypotheses in the cheap fast loop;
any keep/discard decision on a prompt change must cite HUMAN-signed gold. Mark
adjudicates these (minutes, since both records are already 100% coverage-reviewed).

Writes {record}.provisional.json next to this script, and verifies every span
locates in the record's materialised pre-digest so the gold is usable by
`digester eval --gold-json` the moment it is written.
"""

import json
from pathlib import Path

from anomalica_common.pre_digest import materialise
from digester.eval import locate, searchable
from digester.record_parser import parse_record

INGESTS = Path("/home/mark/repos/anomalica/ingests/store")
HERE = Path(__file__).resolve().parent

GEMINI7 = "ed66b8e09c6c608649e68c7cfc3ccb63fdc9673bddb9f205347356065df35a8d"
APOLLO17 = "5d0f5e1ee03fcb55f104ee30fcaa0677b3b839d495457e745f01a6aa28547357"

GOLD = {
    GEMINI7: {
        "record": "2026-07-11-audio-nasa-uap-d003a-gemini-7-audio-excerpt-1965",
        "spans": [
            (
                "at the start of the second revolution, we had a reference to several objects that the crew spotted.",
                "Gemini Control: crew spotted several objects at the start of the second revolution",
            ),
            ("This was in the area of Antigua.", "location of the earlier sighting"),
            (
                "It contains references to sighting not only some particles, but as well as an unidentified object, plus the booster.",
                "Gemini Control frames the three distinct things sighted: particles, an unidentified object, the booster",
            ),
            (
                "We have a bogey at 10 o'clock high.",
                "Borman's core report - a bogey at 10 o'clock high",
            ),
            (
                "We have several, looks like debris up here, actual sighting.",
                "Borman confirms an actual sighting, describes it as debris",
            ),
            (
                "We also have the booster in sight.",
                "Borman distinguishes the booster from the bogey - both in sight",
            ),
            (
                "it looks like hundreds of little particles going by from the left out about three to four miles.",
                "Borman: hundreds of particles, three to four miles off",
            ),
            (
                "Looks like a perhaps a vehicle that's disintegrated.",
                "Borman characterises the large object as a disintegrated vehicle",
            ),
            (
                "It's ahead of us at 2 o'clock, slowly tumbling.",
                "Lovell: the object is ahead at 2 o'clock, slowly tumbling",
            ),
            (
                "This is the unidentified object in addition to particles which appear to be headed in a polar orbit",
                "Gemini Control summary: the unidentified object plus particles apparently in a polar orbit",
            ),
            (
                "It was Borman who reported sighting the bogey.",
                "Gemini Control attributes the bogey sighting to Borman",
            ),
        ],
    },
    APOLLO17: {
        "record": "2026-07-11-audio-nasa-uap-d009-apollo-17-audio-excerpt-december-7-1972",
        "spans": [
            (
                "A few very bright particles or fragments or something that go drifting by as we maneuver.",
                "crew's initial report of bright drifting particles",
            ),
            (
                "There's a whole bunch of big ones on my window there.",
                "larger particles at another window",
            ),
            (
                "Looks like the 4th of July out Ron's window.",
                "vivid characterisation of the scene",
            ),
            (
                "They're very jagged, angular, fragmented or tumbling.",
                "key shape description: jagged, angular, fragmented, tumbling",
            ),
            (
                "They look like pieces or something.",
                "crew rejects Ground's 'fluid' reading - they are pieces",
            ),
            (
                "these fragments are not, are tumbling at a very slow rate.",
                "fragments tumbling slowly",
            ),
            (
                "I got the impression maybe they were curved a little bit, as if they might be off the side of the S-4B.",
                "crew hypothesis: the fragments may have come off the S-IVB stage",
            ),
            ("Ice chunk, possibly.", "candidate explanation: ice"),
            ("Maybe there's paint coming off of it.", "candidate explanation: paint"),
            (
                "one of the flags, I thought it was in the S-2, but it might be on the S-4.",
                "Ground control's peeling-flag hypothesis for the fragments",
            ),
            (
                "with the maneuver complete, the fragment field is essentially static, except for very slight tumbling within the fragment.",
                "the fragment field is static after the S-IVB maneuver",
            ),
            (
                "Every once in a while a fragment of considerably higher velocity than the others goes across my window, but that's very rare.",
                "a rare higher-velocity fragment",
            ),
            (
                "there's no apparent relative motion between fragments.",
                "no relative motion between fragments",
            ),
            (
                "they are flat, flake-like particles, some maybe six inches across",
                "Cernan: flat, flake-like particles about six inches across",
            ),
            (
                "most of them seem to be twinkling, and I think for the most part they're all moving away from us",
                "Cernan: the particles twinkle and move away from the spacecraft",
            ),
        ],
    },
}


def main() -> int:
    ok = True
    for content_hash, spec in GOLD.items():
        body = parse_record((INGESTS / f"{content_hash}.v2.md").read_text()).body
        search, idx = searchable(materialise(body))
        spans = []
        for text, why in spec["spans"]:
            located = locate(text, search, idx) is not None
            if not located:
                ok = False
                print(f"  UNLOCATABLE in {spec['record'][:40]}: {text[:70]!r}")
            spans.append({"text": text, "why": why, "locates": located})
        out = {
            "schema": "anomalica/eval-gold/provisional/1",
            "record_content_hash": f"sha256:{content_hash}",
            "record": spec["record"],
            "provisional": True,
            "drafted_by": "model-draft (digester tuning session)",
            "note": (
                "PROVISIONAL model-drafted highlight gold, pending human "
                "adjudication. May SCREEN hypotheses in the fast loop only; "
                "keep/discard decisions must cite human-signed gold (ADR 0042)."
            ),
            "spans": spans,
        }
        path = HERE / f"{spec['record']}.provisional.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        located_n = sum(1 for s in spans if s["locates"])
        print(
            f"{spec['record'][:52]:52}  {located_n}/{len(spans)} spans locate  -> {path.name}"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
