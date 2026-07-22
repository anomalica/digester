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
