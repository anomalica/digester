import hashlib

import pytest

from digester.eval import (
    context_dependencies,
    grade_digest,
    parse_context_chains,
    parse_highlights,
)
from digester.highlight_gold import HighlightGoldError, review_batch, validate


HASH = "sha256:" + "a" * 64
STAMP = "2026-09-14T12:00:00Z"
REVIEWER = {"issuer": "github", "subject": "123", "name": "Reviewer"}


def _sidecar(body, units, *, end=None, complete=False):
    review_range = {
        "id": "r1",
        "start": 0,
        "end": len(body) if end is None else end,
        "complete": complete,
        "reviewer": REVIEWER,
        "updated_at": STAMP,
        "units": units,
    }
    if complete:
        review_range["attested_at"] = STAMP
    return {
        "schema": "anomalica/highlight-gold/1",
        "record_hash": HASH,
        "body_sha256": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        "ranges": [review_range],
    }


def test_dangling_and_forward_context_leave_dependent_unresolved():
    body = (
        "{{highlight-start: a}}Earlier{{highlight-end: a}} "
        "{{highlight-start: b}}Dependent{{highlight-end: b}} "
        "{{highlight-context: [b, missing]}} {{highlight-context: [a, b]}}"
    )
    highlights = parse_highlights(body)
    closures, unresolved = context_dependencies(highlights, parse_context_chains(body))

    assert closures["b"] == set()
    assert unresolved["b"] == {"missing"}
    assert unresolved["a"] == {"b"}


def test_authenticated_complete_range_requires_attestation_and_all_units():
    body = "{{highlight-start: h1}}fact{{highlight-end: h1}}"
    document = _sidecar(
        body,
        [{"highlight_id": "h1", "decision": "accept", "facts": ["Fact."]}],
        complete=True,
    )
    assert validate(HASH, body, document)["gold_facts"] == 1

    del document["ranges"][0]["attested_at"]
    with pytest.raises(HighlightGoldError, match="attested_at"):
        validate(HASH, body, document)


def test_unresolved_context_must_be_deferred_and_prevents_completion():
    body = (
        "{{highlight-start: h1}}He reported it.{{highlight-end: h1}}"
        "{{highlight-context: [h1, gone]}}"
    )
    accepted = _sidecar(
        body,
        [{"highlight_id": "h1", "decision": "accept", "facts": ["He reported it."]}],
    )
    with pytest.raises(HighlightGoldError, match="must defer"):
        validate(HASH, body, accepted)


def test_context_outside_complete_range_supports_dependent_without_becoming_a_unit():
    intro = "{{highlight-start: h1}}Alice introduced the case.{{highlight-end: h1}} "
    body = (
        intro
        + "{{highlight-start: h2}}She found a document.{{highlight-end: h2}}"
        + "{{highlight-context: [h2, h1]}}"
    )
    document = _sidecar(
        body,
        [
            {
                "highlight_id": "h2",
                "decision": "accept",
                "facts": ["Alice found a document."],
            }
        ],
        complete=True,
    )
    document["ranges"][0]["start"] = len(intro)

    state = validate(HASH, body, document)

    assert state["ranges"][0]["contained_ids"] == ["h2"]
    assert state["closures"]["h2"] == {"h1"}


def test_review_batch_returns_five_units_context_and_deduplicated_proposals():
    body = " ".join(
        [
            "{{highlight-start: h1}}Alice introduced the case.{{highlight-end: h1}}",
            "{{highlight-start: h2}}She found a document.{{highlight-end: h2}}",
            "{{highlight-context: [h2, h1]}}",
            "{{highlight-start: h3}}third fact{{highlight-end: h3}}",
            "{{highlight-start: h4}}fourth fact{{highlight-end: h4}}",
            "{{highlight-start: h5}}fifth fact{{highlight-end: h5}}",
            "{{highlight-start: h6}}sixth fact{{highlight-end: h6}}",
        ]
    )
    document = _sidecar(body, [])
    claim = {"quote": "She found a document.", "text": "Alice found a document."}
    result = review_batch(
        HASH,
        body,
        document,
        [{"model": "one", "claims": [claim]}, {"model": "two", "claims": [claim]}],
    )

    assert [unit["highlight_id"] for unit in result["units"]] == [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
    ]
    assert result["units"][1]["context"][0]["highlight_id"] == "h1"
    assert result["units"][1]["proposed_facts"] == [
        {
            "fact": "Alice found a document.",
            "quote": "She found a document.",
            "models": ["one", "two"],
        }
    ]
    assert all(
        unit["proposal_status"] == "optional-model-output-not-gold"
        for unit in result["units"]
    )


def test_complete_range_bounds_unsupported_assertion_denominator():
    inside = "{{highlight-start: h1}}accepted evidence{{highlight-end: h1}} unrelated inside."
    body = inside + " outside assertion."
    document = _sidecar(
        body,
        [{"highlight_id": "h1", "decision": "accept", "facts": ["Accepted fact."]}],
        end=len(inside),
        complete=True,
    )
    digest = {
        "claims": [
            {"quote": "accepted evidence", "text": "Accepted fact."},
            {"quote": "unrelated inside", "text": "Unrelated assertion."},
            {"quote": "outside assertion", "text": "Outside assertion."},
        ]
    }
    result = grade_digest(body, digest, gold_document=document, record_hash=HASH)

    assert result["precision_denominator"] == 2
    assert result["unsupported_assertion_count"] == 1
    assert result["unsupported_assertion_rate"] == 0.5
    assert result["precision"] is None
