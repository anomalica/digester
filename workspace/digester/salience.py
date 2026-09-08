"""Scoring the reference roles against evidence that needs no model.

A claim's reference says a node is MENTIONED; the role says what it IS. The
question is whether a model can decide that reliably, and two instruments
answer it without a single call and without a noise floor.

THE SOLE-REFERENCE SET IS GROUND TRUTH, NOT AN ESTIMATE. A claim that
references exactly one node has no other candidate for what it is about, so
that edge is `subject` by definition - 10,343 of the live graph's 83,798 edges,
one in eight. A model marking one of them `setting` or `mentioned` is
unambiguously wrong, and the error rate on them is a clean number. That matters
because the obvious test - compare recall with and without the field - cannot
work: run-to-run recall moves 3.9 points on this corpus, which swamps the size
of degradation a new field actually causes.

THE AMBIENT PRIOR IS THE SECOND INSTRUMENT. A node that is genuinely the
subject of things gets talked about alone; a node that is ambient never is.
Measured on the live graph, the share of a node's edges that are
sole-reference: Whitley Strieber 22%, Richard Hoagland 4%, UAP 11%, UFO 8% -
the two corpus-wide terms sit lowest. So a prompt that marks UFO `subject` at a
high rate is wrong before anyone reads a claim, and this reports that rate
rather than waiting for a human to notice.

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
    """Error rate on edges that cannot be anything but `subject`.

    No model decided these and no threshold is involved: a claim with one
    reference is about that node or it is about nothing.
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
        "wrong": wrong,
        "error_rate": round(wrong / assessed, 4) if assessed else None,
        "wrong_by_role": dict(by_wrong_role),
        "examples": wrong_examples,
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
) -> dict:
    """Whether a corpus-wide term is being marked `subject` too often.

    A node the whole corpus is about is rarely what a single claim is about,
    and the live graph agrees: the two ambient terms have the lowest
    sole-reference share of the top ten nodes. A high subject rate on them is
    the prompt failing in the way that is hardest to see by reading output,
    because each individual call looks defensible.
    """
    mix = role_mix(digests)["per_node"]
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
        if not assessed:
            continue
        rate = counts.get("subject", 0) / assessed
        out[name] = {
            "edges": assessed,
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
        "role_mix": mix["overall"],
        "ambient": ambient_check(digests),
    }
