"""The account layer's deterministic half: binding, the floor, and span parsing.

The model's job is only to say where one telling stops and the next begins.
Everything downstream of that is arithmetic, and arithmetic is what these test.
"""

from __future__ import annotations

from digester import accounts


def acct(start, end, title="A telling", subject="a witness", when="1979", **kw):
    return {
        "title": title,
        "subject": subject,
        "when": when,
        "span_start": start,
        "span_end": end,
        **kw,
    }


def claim(cid, location):
    return {"id": cid, "location": location, "text": "..."}


class TestPositions:
    def test_a_timecoded_claim_resolves_to_seconds(self):
        assert accounts.claim_position("00:04:54.3-00:05:01.0") == 294.3
        assert accounts.claim_position("01:00:00") == 3600.0

    def test_char_and_chapter_spans_resolve_to_their_start(self):
        assert accounts.claim_position("char:1200-1400") == 1200.0
        assert accounts.claim_position("ch4:132-354") == 132.0

    def test_a_prose_location_has_no_position(self):
        """Unbindable, not guessed: a claim in the wrong story is worse than
        one in none."""
        assert accounts.claim_position("page 3, paragraph 2") is None
        assert accounts.claim_position("Foreword, paragraph 4") is None
        assert accounts.claim_position(None) is None


class TestBinding:
    def test_each_claim_lands_in_the_account_that_contains_it(self):
        a = [acct("00:10:00", "00:20:00"), acct("00:30:00", "00:40:00")]
        c = [
            claim("1", "00:11:00-00:11:10"),
            claim("2", "00:19:59-00:20:00"),
            claim("3", "00:31:00-00:31:30"),
        ]
        out = accounts.bind(a, c)
        assert out["counts"]["bound"] == 3
        assert out["per_account"] == {0: 2, 1: 1}
        assert out["claim_to_account"]["3"] == accounts.account_id(a[1], 1)

    def test_a_claim_between_accounts_is_counted_not_forced(self):
        a = [acct("00:10:00", "00:20:00")]
        out = accounts.bind(a, [claim("1", "00:25:00-00:25:10")])
        assert out["counts"] == {"bound": 0, "unbindable": 0, "outside": 1}

    def test_an_unbindable_claim_is_counted_separately_from_one_outside(self):
        a = [acct("00:10:00", "00:20:00")]
        out = accounts.bind(a, [claim("1", "page 3"), claim("2", "00:25:00")])
        assert out["counts"]["unbindable"] == 1 and out["counts"]["outside"] == 1

    def test_a_nested_telling_wins_over_the_passage_around_it(self):
        """The smallest containing span is the most specific one."""
        outer = acct("00:10:00", "00:40:00", title="A long digression")
        inner = acct("00:20:00", "00:25:00", title="The story inside it")
        out = accounts.bind([outer, inner], [claim("1", "00:22:00")])
        assert out["per_account"] == {0: 0, 1: 1}

    def test_a_second_telling_of_one_account_binds_to_the_same_account(self):
        a = [
            acct(
                "00:10:00",
                "00:15:00",
                also_spans=[{"span_start": "01:00:00", "span_end": "01:05:00"}],
            )
        ]
        out = accounts.bind(a, [claim("1", "00:11:00"), claim("2", "01:02:00")])
        assert out["per_account"] == {0: 2}, "one account, two spans, not two accounts"


class TestPhraseSpans:
    BODY = "Intro text here.\nHe began: I was driving north that night\nand the car stopped dead by the fence line.\nThen something else."

    def test_a_span_given_as_phrases_resolves_against_the_body(self):
        a = acct(
            "I was driving north that night", "the car stopped dead by the fence line"
        )
        span = accounts.resolve_span(a, self.BODY)
        assert span is not None and span[0] < span[1]

    def test_a_phrase_matches_across_the_line_breaks_the_record_inserts(self):
        """The same trap the quote locator hit, handled here from the start."""
        a = acct("I was driving north that night and the car stopped", "something else")
        assert accounts.resolve_span(a, self.BODY) is not None

    def test_an_unfindable_phrase_yields_no_span_rather_than_a_wrong_one(self):
        a = acct("a sentence that is not in this record", "nor is this one")
        assert accounts.resolve_span(a, self.BODY) is None

    def test_a_reversed_span_is_rejected(self):
        assert accounts.resolve_span(acct("00:20:00", "00:10:00"), "") is None


class TestTheFloor:
    def test_a_candidate_with_too_few_claims_is_dropped_with_its_reason(self):
        a = [acct("00:10:00", "00:20:00"), acct("00:30:00", "00:40:00")]
        kept, dropped = accounts.apply_floor(a, {0: 7, 1: 1})
        assert len(kept) == 1 and len(dropped) == 1
        assert dropped[0]["dropped_because"] == "only 1 claims"

    def test_a_candidate_with_no_subject_or_no_anchor_is_dropped(self):
        no_subject = acct("00:10:00", "00:20:00", subject="")
        no_anchor = {**acct("00:30:00", "00:40:00"), "when": "", "where": ""}
        kept, dropped = accounts.apply_floor([no_subject, no_anchor], {0: 9, 1: 9})
        assert kept == []
        assert [d["dropped_because"] for d in dropped] == [
            "no subject",
            "no when or where",
        ]

    def test_either_a_when_or_a_where_is_enough(self):
        only_where = {
            **acct("00:10:00", "00:20:00"),
            "when": "",
            "where": "northern New Mexico",
        }
        kept, _ = accounts.apply_floor([only_where], {0: 5})
        assert len(kept) == 1

    def test_dropped_candidates_are_returned_not_discarded(self):
        """They are the evidence for whether the floor is set right."""
        _, dropped = accounts.apply_floor([acct("00:10:00", "00:20:00")], {0: 0})
        assert dropped and dropped[0]["title"] == "A telling"


class TestIdentity:
    def test_the_id_comes_from_the_span_not_the_title(self):
        """A title is model-written prose and changes between runs; the passage
        does not, so keying on the title would make two runs disagree about
        which account is which."""
        a1 = acct("00:10:00", "00:20:00", title="A captain's wife taken near Springer")
        a2 = acct("00:10:00", "00:20:00", title="The Springer roadside account")
        assert accounts.account_id(a1, 0) == accounts.account_id(a2, 0)

    def test_conform_emits_the_stored_shape_and_defaults_an_unknown_role(self):
        out = accounts.conform(acct("00:10:00", "00:20:00", teller_role="nonsense"), 0)
        assert out["teller_role"] == "unclear"
        assert out["span"] == "00:10:00-00:20:00" and out["id"].startswith("acct-")
        assert "where" not in out, "an absent field stays absent rather than empty"

    def test_a_valid_role_survives(self):
        out = accounts.conform(
            acct("00:10:00", "00:20:00", teller_role="investigated"), 0
        )
        assert out["teller_role"] == "investigated"


class TestOneEndedSpans:
    """The model tidies a phrase and one end stops matching. An interview is
    sequential, so the next telling's start supplies the missing end."""

    BODY = (
        "AAA start one here. " * 5
        + "BBB start two here. " * 5
        + "CCC start three here. " * 5
    )

    def test_a_missing_end_runs_to_the_next_account(self):
        a = [
            {
                "span_start": "AAA start one here",
                "span_end": "a phrase that is not present",
            },
            {"span_start": "BBB start two here", "span_end": "BBB start two here"},
        ]
        spans = accounts.resolve_all(a, self.BODY)
        assert spans[0], "the first account kept a span"
        assert spans[0][0][1] <= self.BODY.index("BBB"), (
            "it stops where the next begins"
        )

    def test_an_account_with_neither_end_is_still_dropped(self):
        a = [{"span_start": "nowhere at all", "span_end": "nor here"}]
        assert accounts.resolve_all(a, self.BODY) == [[]], (
            "no span invented from nothing"
        )

    def test_a_fully_resolved_span_is_untouched(self):
        a = [{"span_start": "AAA start one here", "span_end": "BBB start two here"}]
        spans = accounts.resolve_all(a, self.BODY)
        assert spans[0][0][0] == self.BODY.index("AAA")
