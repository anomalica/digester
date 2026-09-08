"""Pure (non-LLM) logic in extract.py: response parsing/sanitising, the claims
schema's node-name enum, and document chunking. These are the deterministic,
breakable parts between the model and the data model."""

import json

from anomalica_common.digest import (
    AttestationLevel,
    AttributionMode,
    ExtractedClaim,
    OriginKind,
    ProvenanceChain,
    attribution_mode,
)
from anomalica_common.pre_digest import strip_word_timestamps
from digester.extract import (
    CHUNK_MAX_CHARS,
    _build_chunks,
    _chunk_text,
    _claim_key_v2,
    _format_directory_v2,
    _parse_response,
    build_claims_schema_v2,
)


# --- _claim_key_v2: provenance is part of a claim's identity ---
#
# The two-pass claims dedup is global across chunks. Keyed on content alone it
# silently dropped the LATER assertion of a proposition - which is usually the
# one that finally names its source. Regression case: the Jon Stewart record
# teases "a type two clone from the Tau Ceti star system" in the cold open with
# no provenance, then repeats it 100 minutes later as the content of an email
# from an anonymous DIA source forwarded via an intermediary. The attributed
# instance is the one worth keeping, and content-only dedup threw it away.

_TAU_CETI = "the being was a cloned ebe type two from the tau ceti star system"


def _claim(content, claim_type, attestation, speaker="Stewart, Jon"):
    return {
        "content": content,
        "claim_type": claim_type,
        "attestation": attestation,
        "speaker": speaker,
    }


def test_claim_key_distinguishes_same_proposition_under_different_provenance():
    cold_open = _claim(_TAU_CETI, "testimony", "second_hand")
    from_source = _claim(_TAU_CETI, "hearsay", "third_hand")

    assert _claim_key_v2(cold_open) != _claim_key_v2(from_source)


def test_claim_key_still_collapses_a_genuine_duplicate():
    first = _claim(_TAU_CETI, "testimony", "second_hand")
    verbatim_repeat = _claim(_TAU_CETI.upper(), "testimony", "second_hand")

    assert _claim_key_v2(first) == _claim_key_v2(verbatim_repeat)


def test_attributed_instance_survives_dedup_against_the_bare_one():
    """The bug: the bare cold-open teaser suppressed the sourced re-statement."""
    seen: set = set()
    kept = []
    for claim in (
        _claim(_TAU_CETI, "testimony", "second_hand"),  # cold open, chunk 1
        _claim(_TAU_CETI, "hearsay", "third_hand"),  # DIA email, chunk 3
        _claim(_TAU_CETI.upper(), "testimony", "second_hand"),  # true duplicate
    ):
        key = _claim_key_v2(claim)
        if key in seen:
            continue
        seen.add(key)
        kept.append(claim)

    assert len(kept) == 2
    assert kept[1]["claim_type"] == "hearsay"
    assert kept[1]["attestation"] == "third_hand"


def test_claim_key_treats_a_missing_attestation_as_its_own_frame():
    hedged = _claim(_TAU_CETI, "hearsay", "third_hand")
    unhedged = _claim(_TAU_CETI, "hearsay", None)

    assert _claim_key_v2(hedged) != _claim_key_v2(unhedged)


# --- provenance_chain: required by the schema, attestation derived from it (ADR 0044) ---


def test_claims_schema_requires_a_provenance_chain():
    """The forcing function. Optional fields get skipped; required ones cannot be.

    Extraction runs under --json-schema, so this is what physically stops a claim
    being emitted without answering where the assertion came from.
    """
    item = build_claims_schema_v2(["Tau Ceti star system"])["properties"]["claims"][
        "items"
    ]

    assert "provenance_chain" in item["required"]
    chain = item["properties"]["provenance_chain"]
    assert set(chain["required"]) == {"origin_kind", "origin", "relay"}
    assert set(chain["properties"]["origin_kind"]["enum"]) == {
        "speaker",
        "named",
        "anonymous",
        "document",
        "unattributed",
    }


def test_attestation_is_derived_from_chain_depth_not_the_models_word():
    """The Tau Ceti chain is four removes; the model graded it second_hand."""
    chain = ProvenanceChain(
        origin_kind=OriginKind.anonymous,
        origin="a person claiming to work inside the Defense Intelligence Agency",
        relay=["an email", "an intermediary known to the speaker"],
    )

    assert chain.attestation() is AttestationLevel.third_hand


def test_attestation_derivation_across_the_chain_kinds():
    speaker_own = ProvenanceChain(origin_kind=OriginKind.speaker, relay=[])
    one_remove = ProvenanceChain(
        origin_kind=OriginKind.named, origin="Fravor, David", relay=["told the speaker"]
    )
    narration = ProvenanceChain(origin_kind=OriginKind.unattributed)

    assert speaker_own.attestation() is AttestationLevel.first_hand
    assert one_remove.attestation() is AttestationLevel.second_hand
    # No evidential stance to record - not a default of first_hand.
    assert narration.attestation() is None


def test_a_digest_written_before_0044_still_parses():
    """Absence of a chain means 'not captured', never 'no chain' - it must not crash."""
    legacy = ExtractedClaim(content="x", claim_type="testimony")

    assert legacy.provenance_chain is None


# --- attribution_mode: one rule, computed once, failing CLOSED (ADR 0044) ---
#
# This drifted to fail-open twice in one session because it lived in prose in
# three repos. It now lives in one function, and these tests are the contract.


def _mode(**kw):
    base = dict(
        claim_type="testimony",
        attestation=None,
        origin_kind="speaker",
        attribution_in_text=False,
        has_chain=True,
    )
    base.update(kw)
    return attribution_mode(**base)


def test_a_claim_with_no_chain_is_never_assertable():
    """Every pre-0044 claim. Absence of a danger signal is not evidence of safety."""
    assert _mode(has_chain=False) is AttributionMode.unknown


def test_a_bare_anonymous_assertion_can_never_reach_bare_ok():
    """The Tau Ceti hole, re-entered through the declared flag.

    The model was required to inline the attribution and declared that it did
    not. Neither branch is safe: asserting it publishes a rumour as fact, and
    rendering it "as written" emits the same bare rumour. Fail closed.
    """
    slipped = _mode(
        origin_kind="anonymous", attestation="third_hand", attribution_in_text=False
    )

    assert slipped is AttributionMode.unknown


def test_hearsay_declared_bare_also_fails_closed():
    assert _mode(claim_type="hearsay", attribution_in_text=False) is (
        AttributionMode.unknown
    )


def test_the_declared_flag_leads_when_the_model_does_its_job():
    honoured = _mode(
        origin_kind="anonymous", attestation="third_hand", attribution_in_text=True
    )

    assert honoured is AttributionMode.in_text


def test_plain_narration_still_renders_as_a_bare_fact():
    """`unattributed` is a POSITIVE signal - the source offers no attribution.

    Quite different from us not knowing. Otherwise "the Nimitz incident occurred
    in 2004" gets hedged into absurdity, which is its own dishonesty.
    """
    assert _mode(origin_kind="unattributed", attribution_in_text=False) is (
        AttributionMode.bare_ok
    )


def test_must_carry_is_a_veto_and_never_manufactures_in_text():
    """It can only ever downgrade a declared false - never upgrade a declared true."""
    not_load_bearing = _mode(
        claim_type="observation", origin_kind="speaker", attribution_in_text=False
    )

    assert not_load_bearing is AttributionMode.bare_ok


# --- _parse_response: sanitises model JSON into the data model ---


def test_parse_response_happy_path():
    raw = json.dumps(
        {
            "record_title": "T",
            "nodes": [{"name": "Fravor, David", "node_type": "person"}],
            "claims": [
                {
                    "content": "He saw it.",
                    "original_excerpt": "  I saw it.  ",
                    "claim_type": "testimony",
                    "attestation": "first_hand",
                    "node_references": ["Fravor, David"],
                    "confidence": 0.9,
                }
            ],
            "extraction_complete": True,
        }
    )
    r = _parse_response(raw)
    assert r.record_title == "T"
    assert r.extraction_complete is True
    assert len(r.nodes) == 1 and r.nodes[0].name == "Fravor, David"
    c = r.claims[0]
    assert c.original_excerpt == "I saw it."  # stripped
    assert c.node_references == ["Fravor, David"]
    assert c.confidence == 0.9


def test_parse_response_skips_invalid_node_type():
    raw = json.dumps(
        {
            "nodes": [
                {"name": "X", "node_type": "not_a_type"},
                {"name": "Y", "node_type": "place"},
            ],
            "claims": [],
        }
    )
    r = _parse_response(raw)
    assert [n.name for n in r.nodes] == ["Y"]  # invalid type dropped


def test_parse_response_defaults_bad_claim_type_and_attestation():
    raw = json.dumps(
        {
            "nodes": [],
            "claims": [
                {"content": "c", "claim_type": "bogus", "attestation": "nonsense"}
            ],
        }
    )
    c = _parse_response(raw).claims[0]
    assert c.claim_type.value == "administrative"  # fallback
    assert c.attestation.value == "first_hand"  # fallback


def test_parse_response_coerces_non_list_refs():
    raw = json.dumps(
        {"nodes": [], "claims": [{"content": "c", "node_references": "oops"}]}
    )
    assert _parse_response(raw).claims[0].node_references == []


def test_parse_response_empty_excerpt_becomes_none():
    raw = json.dumps(
        {"nodes": [], "claims": [{"content": "c", "original_excerpt": "   "}]}
    )
    assert _parse_response(raw).claims[0].original_excerpt is None


# --- build_claims_schema_v2: the node-name enum that constrains refs ---


def test_schema_enums_node_references_to_pass_a_names():
    names = ["Fravor, David", "USS Nimitz"]
    schema = build_claims_schema_v2(names)
    items = schema["properties"]["claims"]["items"]["properties"]["node_references"][
        "items"
    ]
    # The lock moved INSIDE the ref object when roles arrived; it is still a
    # lock, and this asserts where it now lives rather than that it is gone.
    assert items["properties"]["name"]["enum"] == names


def test_schema_without_names_has_no_enum():
    items = build_claims_schema_v2([])["properties"]["claims"]["items"]["properties"][
        "node_references"
    ]["items"]
    assert "enum" not in items["properties"]["name"]
    assert items["properties"]["name"]["type"] == "string"


def test_schema_requires_core_claim_fields():
    req = build_claims_schema_v2(["X"])["properties"]["claims"]["items"]["required"]
    # ADR 0044 added two to the core set: a claim may not be emitted without
    # stating where the assertion came from, nor without declaring whether its own
    # text names who asserted it.
    assert set(req) == {
        "content",
        "category",
        "claim_type",
        "provenance_chain",
        "attribution_in_text",
    }


# --- v2 word-timestamp stripping (record/2 bodies are ~65% timing tokens) ---


def test_strip_word_timestamps_removes_tokens_keeps_words():
    body = "00:00:00.1 {{t:0.11}}Folks, {{t:0.65}}it {{t:0.85}}isn't every day."
    assert strip_word_timestamps(body) == "00:00:00.1 Folks, it isn't every day."


def test_strip_word_timestamps_noop_on_plain_text():
    plain = "A web article with no timing tokens at all."
    assert strip_word_timestamps(plain) == plain


def test_strip_word_timestamps_handles_decimals_and_integers():
    assert strip_word_timestamps("{{t:5}}a {{t:12.34}}b") == "a b"


# --- chunking ---


def test_chunk_short_text_is_single_chunk():
    assert _chunk_text("short document") == ["short document"]


def test_chunk_long_text_splits_within_cap_and_reassembles():
    text = "word " * (CHUNK_MAX_CHARS // 2)  # ~2x the cap
    chunks = _chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_MAX_CHARS for c in chunks)
    assert "".join(chunks) == text  # lossless


def test_build_chunks_splits_on_chapter_markers():
    text = "<!-- chapter: 1 -->\nIntro\n<!-- chapter: 2 -->\nBody"
    chunks = _build_chunks(text)
    assert len(chunks) == 2
    assert chunks[0].startswith("<!-- chapter: 1 -->")
    assert chunks[1].startswith("<!-- chapter: 2 -->")


def test_build_chunks_no_markers_falls_back_to_char_window():
    assert _build_chunks("plain prose, no chapters") == ["plain prose, no chapters"]


# --- directory formatting for the claims prompt ---


def test_format_directory_includes_type_name_and_dates():
    nodes = [
        {
            "name": "Nimitz encounter",
            "type": "event",
            "metadata": {"date_start": "2004-11-10", "date_end": "2004-11-16"},
        },
        {"name": "Fravor, David", "node_type": "person"},
    ]
    out = _format_directory_v2(nodes)
    assert "Nimitz encounter" in out
    assert "date_start=2004-11-10" in out and "date_end=2004-11-16" in out
    assert "person" in out and "Fravor, David" in out


def test_pass_a_stops_when_the_node_directory_outgrows_the_route(monkeypatch):
    """The directory travels in Pass A's PROMPT, not its schema.

    A book failed at chunk 18 of 27 with 473 accumulated nodes and malformed
    JSON - never reaching Pass B, where the enum guard sits. A guard that only
    watches the schema would let exactly that happen again.
    """
    import pytest
    from anomalica_common.llm import RouteEnumLimit, check_route_capacity

    with pytest.raises(RouteEnumLimit) as e:
        check_route_capacity("opencode-go/kimi-k3", 473)
    assert e.value.members == 473

    check_route_capacity("opencode-go/kimi-k3", 22)  # at the limit, permitted
    check_route_capacity("claude-sonnet-5", 5000)  # native enforcement, no ceiling
    check_route_capacity("deepseek/deepseek-v4-pro", 5000)  # openrouter, unaffected


class TestReferenceRoles:
    """A ref records that a node is MENTIONED; the role records what it IS.

    Without it a setting and a subject are the same edge, so nothing downstream
    can tell Sydney from Roswell or rank two thousand claims.
    """

    def test_the_schema_constrains_both_the_name_and_the_role(self):
        from digester.extract import CLAIM_REF_ROLES, build_claims_schema_v2

        item = build_claims_schema_v2(["Kevin Day", "USS Princeton"])["properties"][
            "claims"
        ]["items"]["properties"]["node_references"]["items"]
        assert item["required"] == ["name", "role"]
        assert item["properties"]["name"]["enum"] == ["Kevin Day", "USS Princeton"]
        assert item["properties"]["role"]["enum"] == list(CLAIM_REF_ROLES)

    def test_the_roles_are_ordered_from_most_to_least_central(self):
        from digester.extract import CLAIM_REF_ROLES

        assert CLAIM_REF_ROLES == ("subject", "participant", "setting", "mentioned")

    def test_the_deletion_tests_are_in_the_prompt_verbatim(self):
        from digester import prompt_registry

        t = prompt_registry.prompt_text("claims", "DIGESTER_CLAIMS_PROMPT_FILE")
        assert "delete the node and the claim has no subject left" in t
        assert "Delete it and nothing the claim asserts changes" in t
        assert "judging importance" in t, "the test is mechanical, not a judgement"
