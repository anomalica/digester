#!/usr/bin/env python3
"""Automated quality assessment of digester output YAML.

Heuristic flags per claim, aggregated per model:
  imperial   - non-metric units in claim text (we standardise on metric)
  american   - American spellings (we use British English)
  compound   - looks like several facts merged into one claim (atomicity)
  vague      - non-durable references that need context to be understood
Returns per-claim flags and per-model totals. Heuristics, not judgements -
they point a human at suspect claims, they do not decide correctness.
"""

import re
import sys

import yaml

IMPERIAL = re.compile(
    r"\b\d[\d,\.]*\s*(?:miles per hour|mph|miles?|feet|foot|ft|inches?|pounds?|lbs?|"
    r"Fahrenheit|gallons?|yards?)\b|\bmiles per hour\b",
    re.I,
)
KNOTS = re.compile(r"\bknots?\b", re.I)

# American spelling -> flag the American form (whole word, case-insensitive).
# NB: "defense"/"offense" deliberately excluded - they appear almost entirely inside
# US proper nouns (Department of Defense, Secretary of Defense) that must NOT be Briticised.
AMERICAN = re.compile(
    r"\b(?:categoriz\w*|organiz\w*|recogniz\w*|analyz\w*|emphasiz\w*|realiz\w*|"
    r"minimiz\w*|maximiz\w*|prioritiz\w*|color|colored|behavior\w*|favor\w*|honor\w*|"
    r"traveled|traveling|modeled|modeling|labeled|canceled|"
    r"meters?|centers?|theaters?|maneuver\w*|aluminum|fiber)\b",
    re.I,
)


def imperial_hits(text):
    """All imperial matches in claim text. Per project decision, the claim text
    must be SI only - the original unit belongs in the quote, NOT in parentheses
    after the metric value. So parenthetical imperial is also a fault."""
    return list(IMPERIAL.finditer(text))


# abbreviated units in content (should be full names: "kilometres per hour" not "km/h")
ABBREV_UNIT = re.compile(
    r"\b\d[\d,\.]*\s*(?:km/h|kph|km|cm|mm|kg|m/s)\b|\b\d[\d,\.]*\s?m\b"
)
# reporting-verb anchor at the head of a claim (forbidden - speaker is in the tag)
REPORTING_ANCHOR = re.compile(
    r"^\s*[A-Z][\w.\-]+(?:\s+[A-Z][\w.\-]+){0,3}\s+"
    r"(?:stated|said|says|testified|declared|announced|reported|claimed|"
    r"noted|remarked|asserted|recounted|recalled)\s+that\b",
)

# vague / non-durable references (claim must stand alone out of context)
VAGUE_PHRASES = re.compile(
    r"\b(?:the video footage|video footage|the footage|the objects|these objects|"
    r"those objects|the unidentified objects|the same area|the incident|the encounter|"
    r"the event|the program|the report|the document|the meeting)\b",
    re.I,
)
LEADING_DEICTIC = re.compile(r"^\s*(?:This|These|That|Those|It|They|He|She)\b")


def measurements(text):
    return len(
        re.findall(
            r"\b\d[\d,\.]*\s*(?:metres?|kilometres?|km|m|km/h|"
            r"G-forces?|G|degrees?|years?|seconds?|metres? per second)\b",
            text,
            re.I,
        )
    )


def flag_claim(c):
    text = c.get("text", "") or c.get("content", "")
    flags = []
    imp = imperial_hits(text)
    if imp:
        flags.append("imperial:" + ", ".join(set(m.group(0) for m in imp))[:60])
    if ABBREV_UNIT.search(text):
        flags.append(
            "abbrev-unit:"
            + ", ".join(set(m.group(0) for m in ABBREV_UNIT.finditer(text)))[:50]
        )
    if REPORTING_ANCHOR.search(text):
        flags.append("reporting-anchor")
    if KNOTS.search(text):
        flags.append("knots")
    if AMERICAN.search(text):
        flags.append(
            "american:"
            + ", ".join(
                sorted(set(m.group(0).lower() for m in AMERICAN.finditer(text)))
            )[:60]
        )
    # compound: long + many conjunctions/measurements
    n_and = text.lower().count(" and ")
    n_comma = text.count(",")
    n_meas = measurements(text) + len(IMPERIAL.findall(text))
    if (len(text) > 240 and (n_and >= 2 or n_comma >= 3)) or n_meas >= 3:
        flags.append(f"compound(len={len(text)},and={n_and},meas={n_meas})")
    # vague
    vm = VAGUE_PHRASES.findall(text)
    if vm:
        flags.append("vague:" + ", ".join(sorted(set(v.lower() for v in vm)))[:60])
    if LEADING_DEICTIC.match(text):
        flags.append("leading-deictic")
    return flags


def assess(path):
    d = yaml.safe_load(open(path))
    claims = d.get("domain_claims", []) or []
    per = []
    tot = {
        "imperial": 0,
        "abbrev-unit": 0,
        "reporting-anchor": 0,
        "knots": 0,
        "american": 0,
        "compound": 0,
        "vague": 0,
        "leading-deictic": 0,
    }
    lengths = []
    for c in claims:
        f = flag_claim(c)
        per.append((c, f))
        lengths.append(len(c.get("text", "") or ""))
        for k in tot:
            if any(x.startswith(k) for x in f):
                tot[k] += 1
    tot["n_claims"] = len(claims)
    tot["avg_len"] = round(sum(lengths) / len(lengths)) if lengths else 0
    return tot, per


if __name__ == "__main__":
    print(
        f"{'model':<24} {'claims':>6} {'imp':>4} {'abbr':>4} {'rptAnc':>6} {'amEng':>5} "
        f"{'cmpd':>5} {'vague':>5} {'deic':>4} {'knot':>4}"
    )
    for arg in sys.argv[1:]:
        label, path = arg.split("=", 1)
        t, _ = assess(path)
        print(
            f"{label:<24} {t['n_claims']:>6} {t['imperial']:>4} {t['abbrev-unit']:>4} "
            f"{t['reporting-anchor']:>6} {t['american']:>5} {t['compound']:>5} "
            f"{t['vague']:>5} {t['leading-deictic']:>4} {t['knots']:>4}"
        )
