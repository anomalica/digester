"""The two-stage entailment check, with the classifiers stubbed.

What is tested is the rule set around the models, not the models: which claims
are eligible, when the second stage fires, what it may overturn, what the field
looks like, and that a digest round-trips through the writer's own dumper.
"""

from __future__ import annotations

import yaml

from digester import entailment as ent

E, N, C = [0.9, 0.08, 0.02], [0.05, 0.9, 0.05], [0.01, 0.04, 0.95]


def _claim(quote, text, speaker=None, location=None, **extra):
    c = {"id": "x", "type": "testimony", "quote": quote, "text": text}
    if speaker:
        c["speaker"] = {"id": "s", "name": speaker}
    if location:
        c["location"] = location
    c.update(extra)
    return c


def _stage(rows, seen=None):
    """A classifier stub returning `rows` in order and recording its inputs."""
    rows = list(rows)

    def probs(pairs):
        if seen is not None:
            seen.extend(pairs)
        assert len(pairs) == len(rows), (pairs, rows)
        return rows

    return probs


PRE = (
    "Intro. "
    + "Alice: The object hovered for ten minutes over the bay. "
    + "Outro. " * 20
)


def test_a_claim_without_a_quote_or_text_is_not_assessed():
    doc = {"domain_claims": [_claim("", "The object hovered."), {"text": "no quote"}]}
    counts = ent.annotate(doc, PRE, _stage([]), _stage([]))
    assert counts["ineligible"] == 2 and counts["assessed"] == 0
    assert all("entailment" not in c for c in doc["domain_claims"])


def test_stage_one_entails_and_contradicts_are_final():
    doc = {
        "domain_claims": [
            _claim(
                "The object hovered for ten minutes.", "The object hovered.", "Alice"
            ),
            _claim(
                "The object hovered for ten minutes.", "The object landed.", "Alice"
            ),
        ]
    }
    seen = []
    counts = ent.annotate(doc, PRE, _stage([E, C], seen), _stage([]))
    a, b = doc["domain_claims"]
    assert a["entailment"] == {
        "label": "entails",
        "score": 0.9,
        "model": ent.STAGE1_MODEL,
        "premise": "quote",
    }
    assert b["entailment"]["label"] == "contradicts"
    assert seen[0][0] == "Alice: The object hovered for ten minutes."
    assert counts["labels"] == {"entails/quote": 1, "contradicts/quote": 1}


def test_only_a_neutral_goes_to_the_window_stage_and_its_verdict_wins():
    doc = {
        "domain_claims": [
            _claim(
                "The object hovered for ten minutes",
                "Alice saw it hover over the bay.",
                "Alice",
            )
        ]
    }
    seen2 = []
    counts = ent.annotate(doc, PRE, _stage([N]), _stage([E], seen2))
    e = doc["domain_claims"][0]["entailment"]
    assert e["label"] == "entails" and e["premise"] == "window"
    assert e["model"] == ent.STAGE2_MODEL
    premise = seen2[0][0]
    assert (
        premise.startswith("Alice: ")
        and "over the bay" in premise
        and "Intro." in premise
    )
    assert counts["labels"] == {"entails/window": 1}


def test_a_neutral_whose_quote_cannot_be_located_stays_at_stage_one():
    doc = {"domain_claims": [_claim("nowhere in the record", "Something.", "Alice")]}
    counts = ent.annotate(doc, PRE, _stage([N]), _stage([]))
    e = doc["domain_claims"][0]["entailment"]
    assert e["label"] == "neutral" and e["premise"] == "quote"
    assert counts["unlocated"] == 1


def test_a_char_location_locates_an_elided_quote_whose_fragment_is_missing():
    a = PRE.index("The object")
    doc = {
        "domain_claims": [
            _claim(
                "misquoted start ... over the bay",
                "Something.",
                location=f"char:{a}-{a + 40}",
            )
        ]
    }
    seen2 = []
    ent.annotate(doc, PRE, _stage([N]), _stage([N], seen2))
    assert "hovered for ten minutes" in seen2[0][0]


def test_an_assessed_claim_is_skipped_unless_forced():
    c = _claim(
        "q",
        "t",
        entailment={"label": "entails", "score": 1.0, "model": "m", "premise": "quote"},
    )
    doc = {"domain_claims": [c]}
    assert ent.annotate(doc, PRE, _stage([]), _stage([]))["skipped"] == 1
    ent.annotate(doc, PRE, _stage([C]), _stage([]), force=True)
    assert c["entailment"]["label"] == "contradicts"


def test_a_malformed_existing_block_is_reassessed():
    c = _claim("q", "t", entailment={"label": "maybe"})
    ent.annotate({"domain_claims": [c]}, PRE, _stage([E]), _stage([]))
    assert c["entailment"]["label"] == "entails"


def test_both_claim_lists_are_covered():
    doc = {
        "domain_claims": [_claim("q1", "t1")],
        "infrastructure_claims": [_claim("q2", "t2")],
    }
    counts = ent.annotate(doc, None, _stage([E, E]), None)
    assert counts["assessed"] == 2


def test_the_yaml_round_trip_keeps_the_field_and_the_rest(monkeypatch):
    text = yaml.safe_dump(
        {
            "schema": "anomalica/digest/1",
            "record": {"content_hash": "sha256:abc"},
            "domain_claims": [
                {
                    "id": "1",
                    "type": "testimony",
                    "quote": "The object hovered for ten minutes",
                    "text": "It hovered.",
                }
            ],
        },
        sort_keys=False,
    )

    class Stub:
        def annotate(self, doc, pre, force=False):
            c = ent.annotate(doc, pre, _stage([E]), _stage([]), force=force)
            c["duration_s"] = 2.5
            return c

        def usage_entries(self, duration_s):
            return [{"stage": "check", "model": "m1", "duration_s": duration_s}]

    out, counts = ent.annotate_yaml(text, PRE, Stub())
    doc = yaml.safe_load(out)
    assert doc["schema"] == "anomalica/digest/1"
    assert doc["ai_usage"] == [{"stage": "check", "model": "m1", "duration_s": 2.5}]
    assert doc["domain_claims"][0]["entailment"]["label"] == "entails"
    assert list(doc["domain_claims"][0].keys())[-1] == "entailment", (
        "appended after text"
    )
    assert counts["assessed"] == 1


def test_locate_folds_curly_quotes_and_takes_the_first_fragment():
    pre = "He said “we saw it” and then left. More text follows here."
    assert ent.locate(pre, "we saw it", None) == (9, 18)
    assert ent.locate(pre, "left ... follows", None) is not None
    assert ent.locate(pre, "absent text", "page 3") is None


def test_needs_check_is_derived_from_the_claims():
    assert ent.needs_check({"domain_claims": [_claim("q", "t")]})
    done = _claim(
        "q",
        "t",
        entailment={"label": "neutral", "score": 0.7, "model": "m", "premise": "quote"},
    )
    assert not ent.needs_check({"domain_claims": [done]})
    assert not ent.needs_check({"domain_claims": [{"text": "no quote"}]})


def test_the_checker_fails_closed_on_a_policy_refusal(monkeypatch):
    import pytest

    monkeypatch.setattr(ent, "policy_refusal", lambda *m: f"{m[0]}: barred")
    with pytest.raises(PermissionError, match="barred"):
        ent.Checker(stage1_model="x/y")


def test_the_policy_permits_the_shipped_pair():
    assert ent.policy_refusal(ent.STAGE1_MODEL, ent.STAGE2_MODEL) is None
    assert ent.policy_refusal("cross-encoder/nli-deberta-v3-base")


def test_an_out_of_memory_batch_halves_and_then_moves_to_the_cpu(monkeypatch):
    torch = __import__("pytest").importorskip("torch")
    c = ent.Classifier("stub", device="cuda", batch_size=4)
    c._model = object()
    c._tok = object()
    calls = []

    def run(batch):
        calls.append((len(batch), c.device))
        if c.device == "cuda":
            raise torch.cuda.OutOfMemoryError("no")
        return [[0.9, 0.05, 0.05]] * len(batch)

    monkeypatch.setattr(c, "_run_batch", run)
    monkeypatch.setattr(c, "_to_cpu", lambda: setattr(c, "device", "cpu"))
    out = c.probs([("a", "b")] * 5)
    assert len(out) == 5 and all(r[0] == 0.9 for r in out)
    assert calls[0] == (4, "cuda") and (1, "cuda") in calls and calls[-1][1] == "cpu"
