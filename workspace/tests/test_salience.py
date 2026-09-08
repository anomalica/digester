"""Scoring reference roles against evidence that needs no model.

The sole-reference set is ground truth by construction, not an estimate: a
claim with one reference is about that node or about nothing. That gives a
clean error rate where the obvious instrument - recall with and without the
field - cannot work, because run-to-run recall moves 3.9 points on this corpus
and swamps the effect.
"""

from __future__ import annotations

from digester import salience


def claim(*refs, text="A claim."):
    return {
        "text": text,
        "refs": [{"name": n, "role": r} if r else {"name": n} for n, r in refs],
    }


def digest(*claims):
    return {"domain_claims": list(claims)}


class TestSoleReference:
    def test_a_single_reference_marked_subject_is_correct(self):
        out = salience.sole_reference_score([digest(claim(("Roswell", "subject")))])
        assert out["sole_reference_edges"] == 1 and out["wrong"] == 0
        assert out["error_rate"] == 0.0

    def test_a_single_reference_marked_anything_else_is_wrong(self):
        d = digest(claim(("Sydney", "setting")), claim(("UFO", "mentioned")))
        out = salience.sole_reference_score([d])
        assert out["wrong"] == 2 and out["error_rate"] == 1.0
        assert out["wrong_by_role"] == {"setting": 1, "mentioned": 1}
        assert out["examples"][0][0] == "Sydney"

    def test_claims_with_several_references_are_not_scored(self):
        """They have no ground truth - that is the whole point of the set."""
        d = digest(claim(("Kevin Day", "subject"), ("San Diego", "setting")))
        assert salience.sole_reference_score([d])["sole_reference_edges"] == 0

    def test_an_unassessed_edge_is_counted_apart_from_a_wrong_one(self):
        """A digest predating the field must not read as 100% wrong."""
        out = salience.sole_reference_score([digest(claim(("Roswell", None)))])
        assert out["unassessed"] == 1 and out["wrong"] == 0
        assert out["error_rate"] is None, "no assessed edges means no rate, not zero"

    def test_the_rate_is_over_assessed_edges_not_all_of_them(self):
        d = digest(claim(("A", "subject")), claim(("B", "setting")), claim(("C", None)))
        out = salience.sole_reference_score([d])
        assert out["assessed"] == 2 and out["error_rate"] == 0.5


class TestAmbientPrior:
    """A node the whole corpus is about is rarely what one claim is about."""

    def test_a_corpus_wide_term_marked_subject_too_often_is_flagged(self):
        d = digest(
            claim(("UFO", "subject"), ("Roswell", "setting")),
            claim(("UFO", "subject"), ("Kevin Day", "subject")),
            claim(("UFO", "mentioned"), ("Nimitz", "subject")),
        )
        out = salience.ambient_check([d], ceiling=0.25)
        assert out["UFO"]["subject_rate"] > 0.25 and out["UFO"]["over_ceiling"]

    def test_an_ambient_term_mostly_marked_mentioned_passes(self):
        d = digest(
            *[claim(("UAP", "mentioned"), ("X", "subject")) for _ in range(9)],
            claim(("UAP", "subject"), ("Y", "setting")),
        )
        out = salience.ambient_check([d], ceiling=0.25)
        assert out["UAP"]["subject_rate"] == 0.1 and not out["UAP"]["over_ceiling"]

    def test_nodes_that_are_not_ambient_are_not_checked(self):
        d = digest(claim(("Kevin Day", "subject")))
        assert salience.ambient_check([d]) == {}

    def test_a_specific_incident_containing_the_word_is_not_ambient(self):
        """Substring matching flagged 'November 1986 Japanese Airlines UFO
        incident, Alaska' at a 93% subject rate as a problem. It is a specific
        incident and being the subject is correct."""
        d = digest(
            *[
                claim(
                    ("November 1986 Japanese Airlines UFO incident, Alaska", "subject")
                )
                for _ in range(9)
            ]
        )
        assert salience.ambient_check([d]) == {}


def test_the_report_carries_all_three_instruments():
    d = digest(
        claim(("Roswell", "subject")), claim(("UFO", "subject"), ("X", "setting"))
    )
    out = salience.report([d])
    assert set(out) == {"sole_reference", "role_mix", "ambient"}
    assert out["role_mix"]["subject"] == 2 and out["role_mix"]["setting"] == 1


class TestTheConfound:
    """The sole-reference set is not an accuracy measure, and was built as one.

    A claim with one reference is about that node only if the claim's real
    subject HAS a node. Read against a real digest, most apparent errors were
    the model applying the definitions correctly to a claim whose subject was
    never extracted.
    """

    def test_the_headline_number_is_named_an_upper_bound(self):
        d = digest(claim(("Kirtland Air Force Base", "setting")))
        out = salience.sole_reference_score([d])
        assert out["error_upper_bound"] == 1.0
        assert "error_rate" not in out, "the name promised more than it carried"

    def test_the_same_count_is_reported_as_under_nodding(self):
        """A sole-reference claim whose one ref is properly a setting is a claim
        whose SUBJECT is missing from the graph - a free measure of
        under-extraction that nothing else provides."""
        d = digest(claim(("Kirtland Air Force Base", "setting")))
        assert salience.sole_reference_score([d])["under_nodded"] == 1


class TestSubjectFirst:
    def test_a_reference_that_opens_the_claim_should_be_the_subject(self):
        d = digest(
            claim(
                ("Raymond Fowler", "subject"),
                text="Raymond Fowler investigated the case.",
            ),
            claim(
                ("Raymond Fowler", "setting"),
                text="Raymond Fowler investigated the case.",
            ),
        )
        out = salience.subject_first_score([d])
        assert out["subject_first_edges"] == 2 and out["wrong"] == 1
        assert out["error_rate"] == 0.5

    def test_a_claim_whose_subject_has_no_node_is_excluded(self):
        """The confound the set exists to avoid: the reference is not named
        first, so it is probably not the subject."""
        d = digest(
            claim(
                ("USA, New Mexico, Kirtland Air Force Base", "setting"),
                text="Disc-shaped objects landed at Kirtland Air Force Base.",
            )
        )
        assert salience.subject_first_score([d], window=20)["subject_first_edges"] == 0
