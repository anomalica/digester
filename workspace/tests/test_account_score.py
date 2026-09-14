from __future__ import annotations

from digester.account_score import _optimal_matches, score


def test_scorer_emits_matches_misses_extras_and_binding_coverage():
    gold = {
        "accounts": [
            {"title": "The Sandia guard encounter", "subject": "a Sandia guard"},
            {"title": "The missing account", "subject": "another witness"},
        ]
    }
    predicted = {
        "accounts": [
            {
                "id": "sandia",
                "title": "Sandia guard's encounter",
                "subject": "the Sandia security guard",
            },
            {"id": "extra", "title": "A wrestling match", "subject": "Doty"},
        ],
        "binding": {"bound": 7, "unbindable": 1, "outside": 2},
    }
    result = score(gold, predicted)
    assert result["counts"] == {"gold": 2, "predicted": 2, "matched": 1}
    assert result["precision"] == result["recall"] == 0.5
    assert result["matches"][0]["shared_title_terms"] == [
        "encounter",
        "guard",
        "sandia",
    ]
    assert result["missed"][0]["title"] == "The missing account"
    assert result["extra"][0]["id"] == "extra"
    assert result["claim_binding"]["coverage"] == 0.7
    assert result["claim_binding"]["resolvable_coverage"] == 0.7778


def test_matching_is_one_to_one_even_when_two_predictions_resemble_one_gold():
    gold = {
        "accounts": [{"title": "Mario Woods interrogation", "subject": "Mario Woods"}]
    }
    predicted = {
        "accounts": [
            {"title": "Mario Woods interrogation", "subject": "Mario Woods"},
            {"title": "Mario Woods interview", "subject": "Mario Woods"},
        ]
    }
    result = score(gold, predicted)
    assert result["counts"]["matched"] == 1
    assert len(result["extra"]) == 1


def test_boundary_overlap_is_scored_only_when_both_ranges_are_available():
    gold = {
        "accounts": [
            {
                "title": "A bounded incident",
                "subject": "a witness",
                "approx_line_start": 10,
                "approx_line_end": 20,
            }
        ]
    }
    predicted = {
        "accounts": [
            {
                "title": "The bounded incident",
                "subject": "the witness",
                "line_start": 15,
                "line_end": 25,
            }
        ]
    }
    result = score(gold, predicted)
    assert result["boundary_overlap"] == {
        "available": 1,
        "matched": 1,
        "mean_iou": 0.375,
    }
    assert result["matches"][0]["boundary_overlap"]["intersection_lines"] == 6


def test_boundary_range_can_be_derived_from_a_stored_verbatim_span():
    body = "intro\nthe account starts here\ndetail\nthe account ends here\noutro"
    gold = {
        "accounts": [
            {
                "title": "The bounded account",
                "subject": "a witness",
                "approx_line_start": 2,
                "approx_line_end": 4,
            }
        ]
    }
    predicted = {
        "accounts": [
            {
                "title": "A bounded account",
                "subject": "the witness",
                "span": "the account starts here-the account ends here",
            }
        ]
    }
    result = score(gold, predicted, source_body=body)
    assert result["boundary_overlap"]["mean_iou"] == 1.0


def test_boundary_intersection_cannot_create_a_semantically_unsupported_match():
    body = "the shared passage begins\nand continues\nthe shared passage ends"
    gold = {
        "accounts": [
            {
                "title": "Passengers taken to the S2 Annex",
                "subject": "two visitors in a van crash",
                "approx_line_start": 1,
                "approx_line_end": 3,
            }
        ]
    }
    predicted = {
        "accounts": [
            {
                "title": "Carthusian monks at a Vermont monastery",
                "subject": "an attempted monastery visit",
                "span": "the shared passage begins-the shared passage ends",
            }
        ]
    }
    result = score(gold, predicted, source_body=body)
    assert result["matches"] == []
    assert len(result["missed"]) == len(result["extra"]) == 1


def test_assignment_is_globally_optimal_not_greedy():
    candidates = [
        {"predicted_index": 0, "gold_index": 0, "eligible": True, "score": 0.9},
        {"predicted_index": 0, "gold_index": 1, "eligible": True, "score": 0.8},
        {"predicted_index": 1, "gold_index": 0, "eligible": True, "score": 0.85},
        {"predicted_index": 1, "gold_index": 1, "eligible": False, "score": 0.0},
    ]
    selected = _optimal_matches(candidates, gold_count=2, predicted_count=2)
    assert {(row["predicted_index"], row["gold_index"]) for row in selected} == {
        (0, 1),
        (1, 0),
    }
