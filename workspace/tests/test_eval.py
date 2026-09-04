from digester.eval import (
    ancestor_closures,
    claims_of,
    grade_digest,
    parse_context_chains,
    parse_highlights,
)

BODY = "\n".join(
    [
        "The quick brown fox jumped over the lazy dog.",
        "{{highlight-start: 1}}Aliens landed near the base at midnight.{{highlight-end: 1}}",
        "The weather was fine that day and nothing else happened.",
        "{{highlight-start: 2}}A craft was recovered intact by the Navy.{{highlight-end: 2}}",
    ]
)


def _digest(quotes):
    return {"model": "test", "claims": [{"quote": q, "text": q} for q in quotes]}


def test_parse_highlights_matched_pairs():
    hls = parse_highlights(BODY)
    assert [h["id"] for h in hls] == ["1", "2"]
    assert hls[0]["text"] == "Aliens landed near the base at midnight."
    assert hls[1]["text"] == "A craft was recovered intact by the Navy."


def test_parse_highlights_orphan_start_autocloses():
    body = "before {{highlight-start: 9}}tail with no close marker at all"
    hls = parse_highlights(body)
    assert len(hls) == 1
    assert hls[0]["text"] == "tail with no close marker at all"


def test_parse_highlights_orphan_end_dropped():
    body = "some prose {{highlight-end: 7}} more prose"
    assert parse_highlights(body) == []


def test_parse_highlights_overlap_by_id():
    body = "{{highlight-start: a}}quick {{highlight-start: b}}brown{{highlight-end: a}} fox{{highlight-end: b}}"
    hls = {h["id"]: h["text"] for h in parse_highlights(body)}
    assert hls["a"] == "quick brown"
    assert hls["b"] == "brown fox"


def test_parse_context_chains():
    body = "{{highlight-context: [28, 26]}} x {{highlight-context: [29, 28]}}"
    assert parse_context_chains(body) == [["28", "26"], ["29", "28"]]


def test_ancestor_closure_does_not_merge_units():
    # Two dependents share one ancestor hub. Each is its OWN unit with the hub in
    # its closure; the shared ancestor must NOT merge them (anomalica's ruling -
    # union-by-component would wrongly collapse them into one unit).
    chains = [["dep1", "hub"], ["dep2", "hub"]]
    cl = ancestor_closures(["hub", "dep1", "dep2"], chains)
    assert cl["dep1"] == {"hub"}
    assert cl["dep2"] == {"hub"}
    assert cl["hub"] == set()  # the hub itself has no ancestors


def test_ancestor_closure_transitive_and_drops_dangling():
    # c -> b -> a is transitive; "x" is a dangling ref (not a present highlight).
    chains = [["c", "b"], ["b", "a"], ["a", "x"]]
    cl = ancestor_closures(["a", "b", "c"], chains)
    assert cl["c"] == {"a", "b"}
    assert cl["b"] == {"a"}
    assert cl["a"] == set()  # dangling "x" dropped, never a failure


def test_recall_is_coverage_weighted_not_binary():
    # A claim covering only part of a highlight must score the FRACTION, not snap
    # to 100% (anomalica's pin: 1 of 3 facts reads 33%, never a hit). "alpha beta
    # gamma" is ~16 of the highlight's ~30 chars.
    body = "pre {{highlight-start: 1}}alpha beta gamma delta epsilon{{highlight-end: 1}} post"
    r = grade_digest(body, _digest(["alpha beta gamma"]))
    assert r["gold_units"] == 1
    assert (
        0.3 < r["recall"] < 0.85
    )  # partial, NOT 1.0 (would fail on hit/miss counting)


def test_claims_of_both_formats():
    assert len(claims_of({"claims": [{"quote": "a"}]})) == 1
    legacy = {
        "domain_claims": [{"quote": "a"}],
        "infrastructure_claims": [{"quote": "b"}],
    }
    assert len(claims_of(legacy)) == 2


def test_grade_recall_fidelity_offtarget():
    digest = _digest(
        [
            "Aliens landed near the base at midnight.",  # contiguous, covers gold 1
            "The quick brown fox jumped",  # contiguous, off-target
            "A craft was recovered... by the Navy.",  # elided, covers gold 2
            "Elvis was aboard the craft as well.",  # broken - not in source
        ]
    )
    r = grade_digest(BODY, digest)
    assert r["claims"] == 4
    assert r["gold_spans"] == 2
    assert r["recall"] > 0.85  # both gold spans well covered (one via an elided quote)
    assert r["contiguous"] == 2
    assert r["elided"] == 1
    assert r["reordered"] == 0
    assert r["broken"] == 1
    assert abs(r["quote_fidelity"] - 0.75) < 1e-9  # 3 of 4 faithful
    assert r["off_target_count"] == 1  # the fox quote


def test_reordered_fragments_are_a_fidelity_failure():
    # Both fragments are verbatim, but stitched OUT of source order (quote-mining):
    # in the body "A craft was recovered intact" precedes "by the Navy".
    digest = _digest(["by the Navy... A craft was recovered intact"])
    r = grade_digest(BODY, digest)
    assert r["reordered"] == 1
    assert r["elided"] == 0
    assert r["broken"] == 0
    assert r["quote_fidelity"] == 0.0  # reordered is NOT mechanically faithful
    assert r["recall"] == 0.0  # a reordered claim contributes no spans to recall


def test_elided_quote_covers_two_separate_gold_spans():
    # One quote eliding across BOTH highlights must partially cover both.
    digest = _digest(["Aliens landed near the base... recovered intact by the Navy."])
    r = grade_digest(BODY, digest)
    assert all(u["coverage"] > 0 for u in r["unit_coverage"])  # both gold spans reached
    assert r["recall"] > 0.5
    assert r["elided"] == 1
    assert r["broken"] == 0


BODY_COREF = "\n".join(
    [
        "{{highlight-start: a}}Jon Stewart hosted the show.{{highlight-end: a}}",
        "{{highlight-start: b}}He later interviewed a whistleblower.{{highlight-end: b}}",
        "{{highlight-context: [b, a]}}",
    ]
)


def _digest_qt(pairs):
    return {"model": "t", "claims": [{"quote": q, "text": t} for q, t in pairs]}


def test_coref_pass_when_claim_names_referent():
    # Dependent span "He later..." (closure {a}) covered by a claim naming the
    # referent THAT APPEARS IN THE ANCESTOR -> pass, with the match emitted.
    digest = _digest_qt(
        [
            (
                "He later interviewed a whistleblower.",
                "Jon Stewart interviewed a whistleblower.",
            )
        ]
    )
    r = grade_digest(BODY_COREF, digest)
    assert r["coref_applicable"] == 1
    assert r["coref_passed"] == 1
    assert r["coref_audit"][0]["closure_hubs"] == ["a"]
    assert r["coref_audit"][0]["resolved"] is True
    assert "Stewart" in r["coref_audit"][0]["matched"]


def test_coref_fail_when_claim_leaves_bare_pronoun():
    # Same dependent span, but the covering claim echoes the bare pronoun -> the
    # unresolved-'he' failure: applicable but not passed.
    digest = _digest_qt(
        [("He later interviewed a whistleblower.", "He interviewed a whistleblower.")]
    )
    r = grade_digest(BODY_COREF, digest)
    assert r["coref_applicable"] == 1
    assert r["coref_passed"] == 0
    assert r["coref_rate"] == 0.0


def test_no_gold_gives_null_recall():
    r = grade_digest("plain prose with no highlights here", _digest(["plain prose"]))
    assert r["gold_spans"] == 0
    assert r["recall"] is None


def test_external_gold_texts_override_in_body_highlights():
    # gold_texts grades against an external (provisional) gold set, ignoring the
    # record's own {{highlight}} markers.
    digest = _digest(["The weather was fine that day and nothing else happened."])
    r = grade_digest(
        BODY,
        digest,
        gold_texts=["The weather was fine that day and nothing else happened."],
    )
    assert r["gold_spans"] == 1  # the one external span, not the body's two highlights
    assert r["recall"] == 1.0
    assert r["off_target_count"] == 0


def test_coref_applicability_is_the_reviewers_edge_not_our_guess():
    # A span that NAMES someone can still depend on its ancestor for a DIFFERENT
    # referent ("Bob Lazar said it was the company that hired him"). The old rule
    # discarded exactly those - the cases where attribution actually breaks - and
    # selected 10 of Mark's 104 context-bearing units. Applicability is now simply:
    # the reviewer drew a context edge.
    body = "\n".join(
        [
            "{{highlight-start: a}}Jon Stewart hosted the show.{{highlight-end: a}}",
            "{{highlight-start: b}}Bob Lazar said he hired him.{{highlight-end: b}}",
            "{{highlight-context: [b, a]}}",
        ]
    )
    digest = _digest_qt(
        [("Bob Lazar said he hired him.", "Jon Stewart hired Bob Lazar.")]
    )
    r = grade_digest(body, digest)
    assert r["coref_applicable"] == 1  # not filtered out for naming Lazar
    assert r["coref_passed"] == 1  # resolved to "Stewart", named in the ancestor


def test_coref_untestable_when_ancestors_name_nobody():
    # If the linked ancestor names no proper noun there is nothing to resolve TO,
    # so the unit cannot test name-resolution - counted untestable, never a pass.
    body = "\n".join(
        [
            "{{highlight-start: a}}the tape was shown that evening.{{highlight-end: a}}",
            "{{highlight-start: b}}He later described it.{{highlight-end: b}}",
            "{{highlight-context: [b, a]}}",
        ]
    )
    r = grade_digest(
        body, _digest_qt([("He later described it.", "Someone described it.")])
    )
    assert r["coref_applicable"] == 0
    assert r["coref_untestable"] == 1


def test_an_extended_highlight_is_one_gold_unit_joined_by_elision():
    """One id, several start/end pairs: one highlight, one expected claim.

    A reviewer draws one highlight and expects one claim. The evidence is often
    at the top and bottom of a fat paragraph, so highlighting the whole thing
    would tell the grader "one claim from all of this" and stop saying which.

    As a single tuple per id the second pair overwrote the first AND the id was
    appended to `order` twice, so the output was two copies of the SECOND part
    with the first silently gone. Nothing errored; the gold was quietly wrong.
    """
    from digester.eval import parse_highlights

    body = (
        "intro "
        "{{highlight-start: a1}}the part that matters{{highlight-end: a1}}"
        " a long digression nobody wants in the quote "
        "{{highlight-start: a1}}and its conclusion{{highlight-end: a1}}"
        " outro"
    )
    units = parse_highlights(body)
    assert len(units) == 1, f"extended highlight split into {len(units)} units"
    text = units[0]["text"]
    assert "the part that matters" in text
    assert "and its conclusion" in text
    assert "digression" not in text, "the omitted middle leaked into the gold"
    # joined, never concatenated - otherwise the grader manufactures a sentence
    # the source never uttered
    assert "[...]" in text, text
    assert "matters and its" not in text


def test_coverage_counts_a_character_once_however_many_claims_quote_it():
    """Recall read above 1.0 and rewarded repetition until this was the union."""
    from digester.eval import _overlap

    assert _overlap([0, 100], [(0, 50), (0, 50), (0, 50)]) == 50, "same span thrice"
    assert _overlap([0, 100], [(0, 60), (40, 100)]) == 100, "overlapping, merged"
    assert _overlap([0, 100], [(0, 30), (70, 100)]) == 60, "disjoint, summed"
    assert _overlap([0, 100], [(0, 200)]) <= 100, "never more than the span itself"
    assert _overlap([50, 60], [(0, 200)]) == 10
    assert _overlap([0, 100], []) == 0
