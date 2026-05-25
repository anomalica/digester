import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

from runner import (  # noqa: E402
    build_matcher,
    canon_type,
    match,
    name_variants,
    normalise,
    score,
)


def test_loose_match_rewards_rephrasing_not_noise():
    from runner import build_loose_matcher, loose_match

    lm = build_loose_matcher(
        [
            {
                "canonical": "Patent: Pais - Room Temperature Superconductor",
                "aliases": ["Room Temperature Superconductor patent"],
            }
        ]
    )
    # Rephrased but shares the distinctive tokens -> match
    assert (
        loose_match("Pais's room temperature superconductor patent application", lm)
        == "Patent: Pais - Room Temperature Superconductor"
    )
    # Vaguely related, too few shared distinctive tokens -> no match
    assert loose_match("a patent application", lm) is None
    assert loose_match("Pais", lm) is None


def test_loose_match_only_used_for_loose_types():
    from runner import LOOSE_TYPES

    assert LOOSE_TYPES == {"event", "document", "concept"}
    assert "person" not in LOOSE_TYPES  # strict types must not get loose matching


def test_canon_type_plural_to_singular():
    assert canon_type("people") == "person"
    assert canon_type("organisations") == "organisation"
    assert canon_type("documents") == "document"
    assert canon_type("person") == "person"  # idempotent


def test_score_aligns_plural_found_with_singular_golden():
    golden = {
        "must_find": {
            "person": [{"canonical": "Albert Einstein", "aliases": ["Einstein"]}]
        }
    }
    extraction = {"found": {"people": [{"name": "Einstein"}]}}  # plural key
    s = score(golden, extraction)
    assert s["recall"] == 1.0


def test_normalise_strips_rank_and_punctuation():
    assert normalise("Dr Salvatore Pais") == "salvatore pais"
    assert normalise("Dr. Pais,") == "pais"
    assert normalise("President Donald Trump") == "donald trump"
    assert normalise("the New York Times") == "new york times"


def test_name_variants_reverses_comma_form():
    v = name_variants("Fravor, David")
    assert "fravor david" in v
    assert "david fravor" in v


def test_match_via_alias():
    matcher = build_matcher(
        [{"canonical": "Salvatore Pais", "aliases": ["Dr Pais", "Pais"]}]
    )
    assert match("Dr Salvatore Pais", matcher) == "Salvatore Pais"
    assert match("Pais", matcher) == "Salvatore Pais"
    assert match("Mr. Pais", matcher) == "Salvatore Pais"
    assert match("Unrelated Person", matcher) is None


def test_match_comma_format_against_plain_canonical():
    matcher = build_matcher([{"canonical": "Christopher Mellon", "aliases": []}])
    # pipeline currently emits "Last, First" - must still match
    assert match("Mellon, Christopher", matcher) == "Christopher Mellon"


def test_score_recall_and_precision():
    golden = {
        "must_find": {
            "person": [
                {"canonical": "Salvatore Pais", "aliases": ["Dr Pais"]},
                {"canonical": "Nick Cook", "aliases": ["Cook"]},
            ],
            "object": [{"canonical": "USS Nimitz", "aliases": []}],
        },
        "deny": {"object": ["Three Gorges Dam", "black holes"]},
    }
    extraction = {
        "found": {
            "person": [{"name": "Dr Pais"}, {"name": "Someone Uncurated"}],
            "object": [
                {"name": "USS Nimitz"},
                {"name": "Three Gorges Dam"},  # known noise
                {"name": "Mystery Device"},  # uncurated
            ],
        },
        "total_cost_usd": 0.5,
        "total_elapsed_s": 60,
    }
    s = score(golden, extraction)
    # 2 of 3 must-find matched (Pais, Nimitz); Nick Cook missed
    assert s["total_must_find"] == 3
    assert s["total_matched"] == 2
    assert s["recall"] == round(2 / 3, 3)
    # 1 deny hit of 5 extracted -> precision 1 - 1/5
    assert s["total_extracted"] == 5
    assert s["total_deny_hits"] == 1
    assert s["precision"] == 0.8
    assert s["missed"]["person"] == ["Nick Cook"]
    assert "Three Gorges Dam" in s["deny_hits"]["object"]
    assert "Mystery Device" in s["uncurated"]["object"]


def test_score_perfect_recall_clean_precision():
    golden = {
        "must_find": {
            "person": [{"canonical": "Albert Einstein", "aliases": ["Einstein"]}]
        },
        "deny": {},
    }
    extraction = {"found": {"person": [{"name": "Einstein"}]}}
    s = score(golden, extraction)
    assert s["recall"] == 1.0
    assert s["precision"] == 1.0
    assert s["missed"] == {}


def test_uncurated_not_penalised_in_precision():
    # Golden not exhaustive: an uncurated extraction must NOT lower precision.
    golden = {"must_find": {"person": [{"canonical": "A", "aliases": []}]}, "deny": {}}
    extraction = {"found": {"person": [{"name": "A"}, {"name": "B uncurated"}]}}
    s = score(golden, extraction)
    assert s["precision"] == 1.0  # no deny hits
    assert s["uncurated"]["person"] == ["B uncurated"]
