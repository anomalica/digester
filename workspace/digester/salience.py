"""Scoring the reference roles against evidence that needs no model.

A claim's reference says a node is MENTIONED; the role says what it IS. The
question is whether a model can decide that reliably, and two instruments
answer it without a single call and without a noise floor.

THE SOLE-REFERENCE SET IS NOT GROUND TRUTH. It was built as ground truth on the
argument that a claim referencing exactly one node has no other candidate for
what it is about - 10,343 of the live graph's 83,798 edges. Forty pairs were
then hand-labelled by reading (reports/salience/hand-labels.yaml) and the
argument did not survive: of the five edges the instrument called wrong, five
were correct. Every error it reported was spurious. What it actually detects is
a claim whose subject was never extracted, which leaves a setting as the only
thing to point at - a free measure of UNDER-EXTRACTION, and a good one, but not
an accuracy measure. See `sole_reference_score`.

WHAT THE HAND-LABELLED SET MEASURES INSTEAD. Model role accuracy on that sample
is 85% (91% once the pairs with no defensible answer are dropped), 95% interval
plus or minus 11 points. Two arms would need roughly 100 labelled pairs EACH to
resolve a ten-point difference, so comparing models on this field costs reading
time, not quota. Score any change with reports/salience/score_labels.py.

THE AMBIENT PRIOR IS THE SECOND INSTRUMENT, and it needs a population. A node
that is genuinely the subject of things gets talked about alone; a node that is
ambient never is. Measured on the live graph, the share of a node's edges that
are sole-reference: Whitley Strieber 22%, Richard Hoagland 4%, UAP 11%, UFO 8% -
the two corpus-wide terms sit lowest. That is a statement about the corpus and
says nothing about one record: read within a single record the check fired at
89% on nine edges that were right, and the hand-labelled set contains the same
case (UFO as the subject of "sightings continue to occur", correctly).

Both instruments were found by the assimilator against the existing graph.
"""

from __future__ import annotations

from collections import Counter

ROLES = ("subject", "participant", "setting", "mentioned")


def _refs(claim: dict) -> list[dict]:
    return [r for r in (claim.get("refs") or []) if isinstance(r, dict)]


def claims_of(digest: dict) -> list[dict]:
    return (digest.get("domain_claims") or []) + (
        digest.get("infrastructure_claims") or []
    )


def sole_reference_score(digests: list[dict]) -> dict:
    """Roles on claims that reference exactly one node.

    NOT AN ACCURACY MEASURE, and it was built as one. The assumption - a claim
    with one reference is about that node - holds only when the claim's real
    subject HAS a node. Read against a real digest, most of the apparent errors
    were the model applying the definitions correctly to a claim whose subject
    was never extracted: "Disc-shaped objects landed at Kirtland Air Force
    Base", sole reference Kirtland, marked `setting` - remove Kirtland and the
    claim still asserts that objects landed, which is the setting test verbatim.
    The objects have no node, so the only thing left to point at is where it
    happened.

    So `wrong` here is an UPPER BOUND on error, and the hand-labelled set says
    the quantity of correct answers inside it is not small: all five of the
    edges it called wrong on that sample were right. `under_nodded` is the
    honest reading of the same count: a sole-reference claim whose one reference is properly a
    setting, participant or mention is a claim whose SUBJECT IS MISSING FROM
    THE GRAPH. That is a free measure of under-extraction that nothing else
    provides. See `subject_first_score` for an accuracy measure that is not
    confounded this way.
    """
    total = wrong = unassessed = 0
    wrong_examples: list[tuple[str, str, str]] = []
    by_wrong_role: Counter = Counter()
    for d in digests:
        for c in claims_of(d):
            refs = _refs(c)
            if len(refs) != 1:
                continue
            total += 1
            role = refs[0].get("role")
            if role not in ROLES:
                unassessed += 1
                continue
            if role != "subject":
                wrong += 1
                by_wrong_role[role] += 1
                if len(wrong_examples) < 12:
                    wrong_examples.append(
                        (refs[0].get("name", ""), role, (c.get("text") or "")[:90])
                    )
    assessed = total - unassessed
    return {
        "sole_reference_edges": total,
        "assessed": assessed,
        "unassessed": unassessed,
        # An UPPER BOUND on error, not an error count - see the docstring.
        "wrong": wrong,
        "error_upper_bound": round(wrong / assessed, 4) if assessed else None,
        # The same number read the other way: claims whose subject has no node.
        "under_nodded": wrong,
        "under_nodded_rate": round(wrong / assessed, 4) if assessed else None,
        "wrong_by_role": dict(by_wrong_role),
        "examples": wrong_examples,
    }


def subject_first_score(digests: list[dict], window: int = 60) -> dict:
    """Accuracy on claims whose single reference OPENS the claim.

    The confound in `sole_reference_score` is a claim whose subject was never
    extracted, leaving a setting or a participant as the only reference. When
    the sole reference is also the first thing the sentence names, that is a
    cheap approximation of the grammatical subject being present - "Raymond
    Fowler investigated..." rather than "Disc-shaped objects landed at
    Kirtland...". On those, `subject` really is the answer.

    An approximation, and it says so: a sentence can open with a setting
    ("At Kirtland, objects landed"). It is narrower and cleaner than the set it
    replaces, not perfect.
    """
    total = wrong = 0
    examples: list[tuple[str, str, str]] = []
    for d in digests:
        for c in claims_of(d):
            refs = _refs(c)
            if len(refs) != 1:
                continue
            name, role = refs[0].get("name") or "", refs[0].get("role")
            text = (c.get("text") or "").strip()
            if role not in ROLES or not name or not text:
                continue
            head = text[:window].lower()
            first_word = name.split(",")[0].split("(")[0].strip().lower()
            if not first_word or first_word not in head:
                continue
            total += 1
            if role != "subject":
                wrong += 1
                if len(examples) < 10:
                    examples.append((name, role, text[:90]))
    return {
        "subject_first_edges": total,
        "wrong": wrong,
        "error_rate": round(wrong / total, 4) if total else None,
        "examples": examples,
    }


def role_mix(digests: list[dict]) -> dict:
    """How the roles are distributed overall, and per node for the busiest."""
    overall: Counter = Counter()
    per_node: dict[str, Counter] = {}
    for d in digests:
        for c in claims_of(d):
            for r in _refs(c):
                role = r.get("role")
                name = r.get("name") or ""
                overall[role if role in ROLES else "unassessed"] += 1
                per_node.setdefault(name, Counter())[
                    role if role in ROLES else "unassessed"
                ] += 1
    return {"overall": dict(overall), "per_node": per_node}


def ambient_check(
    digests: list[dict],
    ambient: tuple[str, ...] = (
        "UFO",
        "UAP",
        "Unidentified Flying Object (UFO)",
        "Unidentified Aerial Phenomena (UAP)",
        "Unidentified Anomalous Phenomena (UAP)",
    ),
    ceiling: float = 0.25,
    min_records: int = 5,
) -> dict:
    """Whether a corpus-wide term is being marked `subject` too often.

    ACROSS RECORDS, NEVER WITHIN ONE, and that distinction is the whole check.
    The prior is a corpus statistic: UFO carries 1,414 edges across 72 records
    with an 8% sole-reference share, so it cannot be the subject of most claims
    in most records. It says nothing about any single record. On a record that
    is actually about unidentified objects, claims about their shape, their
    behaviour and their frequency are correctly `subject` - read within one
    record the check fired at 89% on nine edges that were right.

    So a node is only evaluated once its edges span `min_records`. Below that
    there is no population to be ambient across, and the check stays silent
    rather than guessing.
    """
    mix = role_mix(digests)["per_node"]
    seen_in: dict[str, set[int]] = {}
    for i, d in enumerate(digests):
        for c in claims_of(d):
            for r in _refs(c):
                seen_in.setdefault(r.get("name") or "", set()).add(i)
    out = {}
    for name, counts in mix.items():
        # EXACT names, not a substring. Matching any node containing "UFO"
        # flagged "November 1986 Japanese Airlines UFO incident, Alaska" at a
        # 93% subject rate as though it were a problem - it is a specific
        # incident and being the subject is correct. The prior is about
        # CORPUS-WIDE terms that name the whole field, and a specific event
        # that happens to contain one of those words is not one.
        if name.strip().lower() not in {a.strip().lower() for a in ambient}:
            continue
        assessed = sum(v for k, v in counts.items() if k in ROLES)
        records = len(seen_in.get(name, ()))
        if not assessed or records < min_records:
            continue
        rate = counts.get("subject", 0) / assessed
        out[name] = {
            "edges": assessed,
            "records": records,
            "subject_rate": round(rate, 3),
            "over_ceiling": rate > ceiling,
        }
    return out


def report(digests: list[dict]) -> dict:
    """Everything the tenth cell should print, in one call."""
    sole = sole_reference_score(digests)
    mix = role_mix(digests)
    return {
        "sole_reference": sole,
        "subject_first": subject_first_score(digests),
        "role_mix": mix["overall"],
        "ambient": ambient_check(digests),
    }
