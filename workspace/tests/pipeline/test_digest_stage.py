"""The digestion stage: two-pass extraction through to the emitted digest.

Runs `digester.extract.extract_two_pass` over the two fixture records with the
one model call replaced by the canned responses in `fixtures/responses.py`,
hands the result to `two_pass_result_to_yaml`, and asserts on the digest that
comes out.

SCOPE, so nobody reads more into a green run than is there: this covers
PLUMBING, not extraction quality. Every response is written by hand, so a prompt
change that destroys recall passes this file green. What it does prove is that a
claim the model emits survives both passes, the dedup key, the split into domain
and infrastructure, the attestation derivation and the YAML writer without being
dropped, duplicated or silently reshaped.

Where the shared corpus cannot express what a test needs - a deliberate
duplicate, or a claim with no provenance chain at all - the claims response is
written here and validated against the same live schema the shared one is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from anomalica_common.digest.yaml_format import (
    parse_digest_yaml,
    two_pass_result_to_yaml,
)
from digester import extract

from .fixtures import responses as canned
from .fixtures.documents import DOCUMENT_A, DOCUMENT_B

# The corpus validator, shared rather than reimplemented: a second validator
# drifts from the first and then agrees with a fixture the real one rejects.
from .fixtures.responses import _validate as validate_against_live_schema

MODEL = "claude-sonnet-5"


class Digested:
    """A digest, addressed the way an assertion wants to address one."""

    def __init__(self, result: dict, text: str):
        self.result = result
        self.text = text
        self.doc = yaml.safe_load(text)

    @property
    def domain(self) -> list[dict]:
        return self.doc.get("domain_claims") or []

    @property
    def infrastructure(self) -> list[dict]:
        return self.doc.get("infrastructure_claims") or []

    @property
    def claims(self) -> list[dict]:
        return self.domain + self.infrastructure

    def by_text(self, fragment: str) -> dict:
        found = [c for c in self.claims if fragment in (c.get("text") or "")]
        assert len(found) == 1, f"{fragment!r} matched {len(found)} claims, wanted 1"
        return found[0]

    def node(self, name: str) -> dict:
        found = [n for n in self.doc["nodes"] if n["name"] == name]
        assert len(found) == 1, f"node {name!r} appears {len(found)} times, wanted 1"
        return found[0]


def _emit(parsed, result, **yaml_kwargs) -> Digested:
    defaults = {
        "record_title": parsed.title,
        "record_content_hash": parsed.metadata.get("content_hash"),
        "model": MODEL,
    }
    text = two_pass_result_to_yaml(result, **{**defaults, **yaml_kwargs})
    return Digested(result, text)


def digest_of(doc, **yaml_kwargs) -> Digested:
    """Digest a fixture record. Requires the `stubbed_model` fixture."""
    parsed = doc.parsed()
    result = extract.extract_two_pass(parsed.body, model=MODEL)
    return _emit(parsed, result, **yaml_kwargs)


def canned_claims(doc, key: str) -> dict:
    """One claim from the shared corpus, found by a fragment of its content."""
    claims = canned.RESPONSES[(doc.key, "claims")]["claims"]
    found = [c for c in claims if key in c["content"]]
    assert len(found) == 1, f"{key!r} matched {len(found)} canned claims, wanted 1"
    return found[0]


# ---------------------------------------------------------------------------
# Claims responses written here, for shapes the shared corpus cannot carry.
# Node names are document A's, so refs stay inside the enum the claims schema
# builds from pass A.
# ---------------------------------------------------------------------------

NODE_NAMES_A = [n["name"] for n in canned.RESPONSES[(DOCUMENT_A.key, "nodes")]["nodes"]]

PROPOSITION = (
    "The Skerrivore Point radar return was held on two separate scopes at once."
)


def _chain(
    origin_kind: str, origin: str = "", relay=None, origin_ref: str = ""
) -> dict:
    chain = {"origin_kind": origin_kind, "origin": origin, "relay": list(relay or [])}
    if origin_ref:
        chain["origin_ref"] = origin_ref
    return chain


def _claim(content: str, **overrides) -> dict:
    claim = {
        "content": content,
        "category": "domain",
        "claim_type": "observation",
        "attribution_in_text": True,
        "provenance_chain": _chain("speaker", "Dr Helena Marsh", []),
        "node_references": [
            {"name": "Skerrivore Point radar return", "role": "subject"}
        ],
    }
    claim.update(overrides)
    return claim


def _proposition(**overrides) -> dict:
    return _claim(PROPOSITION, **overrides)


def _round(claims: list[dict], complete: bool = True) -> dict:
    return {"claims": claims, "extraction_complete": complete}


# DELIBERATELY OUTSIDE the current claims schema, which requires a provenance
# chain. The shape still arrives from the call cache and from every pre-ADR-0044
# digest, and `two_pass_result_to_yaml` has an explicit branch for it, so the
# branch is exercised by a response that says in its own name why it does not
# conform.
LEGACY_CHAINLESS_CLAIM = {
    "content": "The Northern Reach Observatory moved site in 1996.",
    "category": "domain",
    "claim_type": "administrative",
    "attestation": "second_hand",
    "attribution_in_text": False,
}


@pytest.fixture
def bespoke_model(monkeypatch):
    """Serve document A's canned nodes, and claims rounds the test supplies.

    Returns a callable taking the claim rounds. The last round must set
    extraction_complete, or the claims pass keeps asking ITERATION_MAX times.
    """

    def install(claims_rounds: list[dict]):
        assert claims_rounds[-1]["extraction_complete"] is True, (
            "the last claims round must set extraction_complete"
        )
        rounds = list(claims_rounds)
        nodes = canned.RESPONSES[(DOCUMENT_A.key, "nodes")]

        def serve(preamble, document, task, model, schema=None, use_api=False):
            if canned.pass_of(schema) == "nodes":
                return json.dumps(nodes)
            return json.dumps(rounds.pop(0) if len(rounds) > 1 else rounds[0])

        monkeypatch.setattr(extract, "call_with_document", serve)
        return digest_of(DOCUMENT_A)

    return install


# ---------------------------------------------------------------------------
# The seam, and the fixtures that ride on it
# ---------------------------------------------------------------------------


def test_an_unstubbed_model_call_raises_rather_than_reaching_a_provider():
    # Everything below assumes the seam is the only way out. If it stops being
    # so, this fails before a test can quietly spend money proving something else.
    from anomalica_common.llm import transport

    from .conftest import ProviderCallAttempted

    with pytest.raises(ProviderCallAttempted):
        transport.call_with_document("preamble", "document", "task", MODEL)


def test_no_row_this_suite_writes_can_land_in_the_production_spend_ledger():
    # The transport flushes its ledger row at interpreter exit, after every
    # fixture has gone, and `ledger.enabled()`'s PYTEST_CURRENT_TEST guard is
    # NOT set during that flush. Only the import-time redirect holds. A row from
    # a development run of this very file reached the production ledger and had
    # to be deleted by hand, so the guarantee is asserted rather than assumed -
    # on what the module RESOLVES, not on the environment string.
    import os

    from anomalica_common.llm import ledger

    from .conftest import sandbox_dir

    assert not ledger.enabled()
    assert sandbox_dir() in ledger.path().parents
    assert (
        ledger.path()
        != Path(os.path.expanduser("~")) / ".local/share/scheduler/model-dispatch.jsonl"
    )


def test_the_claim_responses_written_here_match_the_live_claims_schema():
    schema = extract.build_claims_schema_v2(NODE_NAMES_A)
    validate_against_live_schema(_round([_proposition()]), schema, "bespoke")
    validate_against_live_schema(
        _round([_proposition(speaker="Ivo Rennick", attestation="second_hand")]),
        schema,
        "bespoke",
    )


def test_the_legacy_chainless_claim_is_deliberately_outside_the_live_schema():
    # If this stops raising, provenance_chain has stopped being required and the
    # back-compat branch in the writer is no longer a back-compat branch.
    schema = extract.build_claims_schema_v2(NODE_NAMES_A)
    with pytest.raises(AssertionError, match="provenance_chain"):
        validate_against_live_schema(_round([LEGACY_CHAINLESS_CLAIM]), schema, "legacy")


def test_the_claims_pass_is_constrained_to_the_names_pass_a_returned(
    stubbed_model, monkeypatch
):
    seen: list[dict] = []
    inner = extract.call_with_document

    def record(preamble, document, task, model, schema=None, use_api=False):
        seen.append(schema)
        return inner(preamble, document, task, model, schema=schema, use_api=use_api)

    monkeypatch.setattr(extract, "call_with_document", record)
    digest_of(DOCUMENT_A)

    claims_schema = [s for s in seen if canned.pass_of(s) == "claims"]
    names = claims_schema[0]["properties"]["claims"]["items"]["properties"][
        "node_references"
    ]["items"]["properties"]["name"]
    assert names["enum"] == NODE_NAMES_A


# ---------------------------------------------------------------------------
# Every claim survives, once, in the right list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc", [DOCUMENT_A, DOCUMENT_B], ids=["a", "b"])
def test_every_claim_the_model_emitted_appears_exactly_once(stubbed_model, doc):
    d = digest_of(doc)
    emitted = [c["content"] for c in canned.RESPONSES[(doc.key, "claims")]["claims"]]
    assert sorted(c["text"] for c in d.claims) == sorted(emitted)


def test_category_selects_the_claim_list(stubbed_model):
    # Document B is the one with claims in both categories.
    d = digest_of(DOCUMENT_B)
    emitted = canned.RESPONSES[(DOCUMENT_B.key, "claims")]["claims"]
    assert [c["text"] for c in d.domain] == [
        c["content"] for c in emitted if c["category"] == "domain"
    ]
    assert [c["text"] for c in d.infrastructure] == [
        c["content"] for c in emitted if c["category"] == "infrastructure"
    ]


def test_category_is_not_itself_emitted_onto_a_claim(stubbed_model):
    # It is the routing decision, not a field. A consumer reading `category` off
    # a digest claim would read None on every one of them.
    d = digest_of(DOCUMENT_B)
    assert all("category" not in c for c in d.claims)
    assert "category:" not in d.text


def test_a_claim_list_with_nothing_in_it_is_omitted_not_written_empty(stubbed_model):
    # Document A has no infrastructure claims, so the key is absent rather than
    # present and empty. A consumer must read the absence as "none", not crash.
    d = digest_of(DOCUMENT_A)
    assert "infrastructure_claims" not in d.doc
    assert d.doc["domain_claims"]


def test_a_node_reference_role_survives_into_the_digest_refs(stubbed_model):
    d = digest_of(DOCUMENT_A)
    source = canned_claims(DOCUMENT_A, "the plate solution placed at")
    claim = d.by_text("the plate solution placed at")
    assert [(r["name"], r.get("role")) for r in claim["refs"]] == [
        (r["name"], r["role"]) for r in source["node_references"]
    ]
    assert {r["role"] for r in claim["refs"]} == set(extract.CLAIM_REF_ROLES)


def test_a_claim_ref_carries_the_id_of_the_node_it_names(stubbed_model):
    # A relation between two artefacts inside one digest, so a field arriving
    # upstream cannot break it.
    d = digest_of(DOCUMENT_A)
    for claim in d.claims:
        for ref in claim.get("refs") or []:
            assert ref["id"] == d.node(ref["name"])["id"]


def test_node_type_and_metadata_reach_the_digest(stubbed_model):
    d = digest_of(DOCUMENT_A)
    event = d.node("Skerrivore Point radar return")
    assert event["type"] == "event"
    assert event["metadata"] == {"date_start": "1994-03-14", "date_end": "1994-03-14"}


def test_terminology_carries_the_main_subject_codenames_and_acronyms(stubbed_model):
    a = digest_of(DOCUMENT_A)
    nodes_a = canned.RESPONSES[(DOCUMENT_A.key, "nodes")]
    assert a.doc["terminology"] == {
        "main_subject": nodes_a["main_subject"],
        "codenames": nodes_a["codenames_to_resolve"],
    }
    b = digest_of(DOCUMENT_B)
    nodes_b = canned.RESPONSES[(DOCUMENT_B.key, "nodes")]
    assert b.doc["terminology"]["acronyms"] == nodes_b["acronyms"]
    assert "codenames" not in b.doc["terminology"]


def test_the_pinned_prompt_provenance_is_emitted_under_the_prompts_key(stubbed_model):
    # The writer renames it: extract_two_pass returns `prompt_provenance`, the
    # digest carries `prompts`.
    d = digest_of(DOCUMENT_A)
    assert d.doc["prompts"] == d.result["prompt_provenance"]
    assert {p["pass"] for p in d.doc["prompts"]} == {"nodes", "claims"}


# ---------------------------------------------------------------------------
# The attestation ladder (ADR 0044). Assert the OUTPUT. Nothing here recomputes
# the rule; a test that re-derives it agrees with a broken implementation.
# ---------------------------------------------------------------------------


def test_a_speaker_chain_with_no_relay_is_first_hand(stubbed_model):
    d = digest_of(DOCUMENT_A)
    assert d.by_text("the plate solution placed at")["attestation"] == "first_hand"


def test_a_named_origin_with_no_relay_is_second_hand_not_first_hand(stubbed_model):
    # An empty relay is not the same as being the speaker. Reading depth alone
    # would promote someone else's account into a first-hand one.
    d = digest_of(DOCUMENT_A)
    assert d.by_text("held the same radar return")["attestation"] == "second_hand"


def test_a_relay_of_one_is_second_hand(stubbed_model):
    d = digest_of(DOCUMENT_A)
    assert d.by_text("boxed and driven off the site")["attestation"] == "second_hand"


def test_a_relay_of_two_is_third_hand(stubbed_model):
    d = digest_of(DOCUMENT_A)
    assert (
        d.by_text("never entered in the receiving log")["attestation"] == "third_hand"
    )


def test_an_unattributed_chain_omits_attestation_entirely(stubbed_model):
    d = digest_of(DOCUMENT_A)
    assert "attestation" not in d.by_text("remain unexplained")


def test_the_attestation_the_model_declared_is_overridden_by_its_chain(stubbed_model):
    # The canned claim declares first_hand over a named origin, which is
    # second_hand. The chain is the evidence; the model's grade is not.
    source = canned_claims(DOCUMENT_A, "wiped at Whitchurch Down")
    assert source["attestation"] == "first_hand"
    d = digest_of(DOCUMENT_A)
    assert d.by_text("wiped at Whitchurch Down")["attestation"] == "second_hand"


def test_an_unattributed_chain_drops_the_attestation_the_model_declared(bespoke_model):
    d = bespoke_model(
        [
            _round(
                [
                    _proposition(
                        provenance_chain=_chain("unattributed"),
                        attestation="first_hand",
                    )
                ]
            )
        ]
    )
    assert "attestation" not in d.by_text(PROPOSITION)


def test_a_claim_with_no_chain_keeps_the_attestation_the_model_declared(bespoke_model):
    d = bespoke_model([_round([LEGACY_CHAINLESS_CLAIM])])
    claim = d.by_text("moved site in 1996")
    assert claim["attestation"] == LEGACY_CHAINLESS_CLAIM["attestation"]
    assert "provenance_chain" not in claim


# ---------------------------------------------------------------------------
# Provenance chains reach the digest
# ---------------------------------------------------------------------------


def test_the_provenance_chain_reaches_the_digest(stubbed_model):
    d = digest_of(DOCUMENT_A)
    source = canned_claims(DOCUMENT_A, "never entered in the receiving log")
    chain = d.by_text("never entered in the receiving log")["provenance_chain"]
    assert chain["origin_kind"] == source["provenance_chain"]["origin_kind"]
    assert chain["origin"] == source["provenance_chain"]["origin"]
    assert chain["relay"] == source["provenance_chain"]["relay"]


def test_origin_ref_survives_into_the_digest(stubbed_model):
    d = digest_of(DOCUMENT_A)
    source = canned_claims(DOCUMENT_A, "boxed and driven off the site")
    claim = d.by_text("boxed and driven off the site")
    assert (
        claim["provenance_chain"]["origin_ref"]
        == (source["provenance_chain"]["origin_ref"])
    )


def test_two_distinct_sources_in_one_record_keep_two_distinct_origin_refs(
    stubbed_model,
):
    # origin_ref may only ever SPLIT. One record naming two unidentified people
    # must arrive downstream as two roots, not one; that count is what
    # independence is later computed from, so collapsing it under-reports
    # nothing and over-reports corroboration.
    d = digest_of(DOCUMENT_A)
    emitted = {
        c["provenance_chain"]["origin_ref"]
        for c in canned.RESPONSES[(DOCUMENT_A.key, "claims")]["claims"]
        if c["provenance_chain"].get("origin_ref")
    }
    assert len(emitted) == 2
    assert {
        c["provenance_chain"]["origin_ref"]
        for c in d.claims
        if (c.get("provenance_chain") or {}).get("origin_ref")
    } == emitted


def test_attribution_in_text_is_emitted_even_when_false(stubbed_model):
    # False is an answer; its absence is not. A consumer must be able to tell
    # "the writer said the text is bare" from "nobody ever said".
    a = digest_of(DOCUMENT_A)
    assert a.by_text("the plate solution placed at")["attribution_in_text"] is True
    b = digest_of(DOCUMENT_B)
    assert b.by_text("held in the director's cabinet")["attribution_in_text"] is False


# ---------------------------------------------------------------------------
# The top-level shape
# ---------------------------------------------------------------------------

FULL_YAML_KWARGS = {
    "record_title": "Skerrivore Point interview",
    "record_producer": "Northern Reach Observatory",
    "record_publisher": "Northern Reach Observatory",
    "record_date": "1994-03-15",
    "record_medium": "video",
    "record_duration": "00:02:10",
    "record_content_hash": f"sha256:{DOCUMENT_A.content_hash}",
    "record_processing_version": "3",
    "record_reference": "NRO/1994/17",
    "record_id": "00000000-0000-4000-8000-000000000001",
    "model": MODEL,
    "ai_usage": [{"pass": "nodes", "input_tokens": 10, "output_tokens": 20}],
    "pre_digest": {"prep_version": 4, "sha256": "c" * 64},
    "schema_enforcement": "native",
    "extraction_config": {"effort": "medium"},
    "review": {"state": "human"},
    "record_extra": {
        "provenance": {"source_url": "https://example.invalid/skerrivore"}
    },
}

EXPECTED_TOP_LEVEL_KEYS = {
    "schema",
    "extracted_at",
    "model",
    "ai_usage",
    "prompts",
    "schema_enforcement",
    "extraction_config",
    "pre_digest",
    "record",
    "terminology",
    "nodes",
    "domain_claims",
    "infrastructure_claims",
}

EXPECTED_RECORD_KEYS = {
    "id",
    "title",
    "producer",
    "publisher",
    "date",
    "medium",
    "duration",
    "content_hash",
    "processing_version",
    "reference",
    "review",
    "provenance",
}


def test_the_top_level_key_set_is_exactly_this(stubbed_model):
    # A key silently appearing or vanishing fails here and nowhere else. If this
    # goes red for a key that SHOULD be there, add it to the set in the change
    # that adds it to the writer.
    d = digest_of(DOCUMENT_B, **FULL_YAML_KWARGS)
    assert set(d.doc) == EXPECTED_TOP_LEVEL_KEYS


def test_the_record_block_key_set_is_exactly_this(stubbed_model):
    # The record block is an allow-list by design: the digest is a locked
    # interchange schema, so a record's whole frontmatter must not pass through.
    d = digest_of(DOCUMENT_B, **FULL_YAML_KWARGS)
    assert set(d.doc["record"]) == EXPECTED_RECORD_KEYS


def test_optional_top_level_blocks_are_absent_rather_than_null(stubbed_model):
    d = digest_of(DOCUMENT_B)
    absent = EXPECTED_TOP_LEVEL_KEYS - set(d.doc)
    assert absent == {
        "ai_usage",
        "schema_enforcement",
        "extraction_config",
        "pre_digest",
    }
    assert ": null" not in d.text


def test_schema_is_the_first_key_in_the_document(stubbed_model):
    d = digest_of(DOCUMENT_A)
    assert next(iter(d.doc)) == "schema"
    assert d.doc["schema"] == "anomalica/digest/1"


def test_run_kind_is_not_written_by_the_yaml_writer(stubbed_model):
    # It is stamped onto the text by digest_store.write / the CLI when the
    # artefact lands, because only the writer knows whether the run was a
    # production one or a comparison. A reader must not expect it here.
    d = digest_of(DOCUMENT_A, **FULL_YAML_KWARGS)
    assert "run_kind" not in d.doc


def test_the_two_pass_path_can_only_produce_a_single_date_never_a_range(stubbed_model):
    # The parser understands date_range; the claims schema has no date_end and
    # the writer only ever emits `date`, so a consumer reading a date_range off
    # a two-pass digest reads nothing. The asymmetry is with the legacy emitters.
    props = extract.build_claims_schema_v2(NODE_NAMES_A)["properties"]["claims"][
        "items"
    ]["properties"]
    assert "date_end" not in props and "date_range" not in props
    d = digest_of(DOCUMENT_A)
    assert d.by_text("the plate solution placed at")["date"] == "1994-03-14"
    assert all("date_range" not in c for c in d.claims)


# ---------------------------------------------------------------------------
# Dedup across iteration rounds (_claim_key_v2)
# ---------------------------------------------------------------------------


def test_the_same_proposition_under_identical_provenance_is_collapsed_to_one(
    bespoke_model,
):
    d = bespoke_model([_round([_proposition(), _proposition()])])
    assert [c["text"] for c in d.claims] == [PROPOSITION]


def test_the_same_proposition_under_a_different_speaker_is_kept_as_two(bespoke_model):
    # The later assertion is usually the one that finally names its source, so a
    # content-only key would drop exactly the instance worth keeping.
    d = bespoke_model([_round([_proposition(), _proposition(speaker="Ivo Rennick")])])
    assert [c["text"] for c in d.claims] == [PROPOSITION, PROPOSITION]
    assert [(c.get("speaker") or {}).get("name") for c in d.claims] == [
        None,
        "Ivo Rennick",
    ]


def test_the_same_proposition_under_a_different_declared_attestation_is_kept(
    bespoke_model,
):
    d = bespoke_model(
        [
            _round(
                [
                    _proposition(attestation="first_hand"),
                    _proposition(attestation="second_hand"),
                ]
            )
        ]
    )
    assert len(d.claims) == 2


def test_the_same_proposition_under_a_different_claim_type_is_kept(bespoke_model):
    d = bespoke_model(
        [
            _round(
                [
                    _proposition(claim_type="observation"),
                    _proposition(claim_type="testimony"),
                ]
            )
        ]
    )
    assert sorted(c["type"] for c in d.claims) == ["observation", "testimony"]


def test_dedup_holds_across_iteration_rounds_not_only_within_one_response(
    bespoke_model,
):
    # The claims pass iterates until a round adds fewer than ITERATION_MIN_NEW
    # claims, and the dedup set is global across those rounds. A per-round set
    # would re-admit everything the previous round already captured.
    first = _round(
        [
            _proposition(),
            _claim("The observatory kept its own copy of the plate register."),
            _claim("The radar return crossed two adjacent sectors."),
        ],
        complete=False,
    )
    second = _round(
        [
            _proposition(),
            _claim("The observatory kept its own copy of the plate register."),
            _claim("The duty log for that night is missing a page."),
        ]
    )
    d = bespoke_model([first, second])
    texts = [c["text"] for c in d.claims]
    assert len(texts) == len(set(texts)) == 4


@pytest.mark.xfail(
    strict=True,
    reason=(
        "_claim_key_v2 keys on the DECLARED attestation and speaker and never on "
        "the chain, so two distinct anonymous sources asserting one proposition "
        "inside one record collapse to a single claim, and the record then "
        "reports one root where it names two. The key has to see origin_ref."
    ),
)
def test_two_distinct_anonymous_sources_asserting_one_proposition_are_not_collapsed(
    bespoke_model,
):
    d = bespoke_model(
        [
            _round(
                [
                    _proposition(
                        claim_type="hearsay",
                        provenance_chain=_chain(
                            "anonymous", "a duty officer", [], origin_ref="officer-1"
                        ),
                    ),
                    _proposition(
                        claim_type="hearsay",
                        provenance_chain=_chain(
                            "anonymous", "a second officer", [], origin_ref="officer-2"
                        ),
                    ),
                ]
            )
        ]
    )
    assert {c["provenance_chain"]["origin_ref"] for c in d.claims} == {
        "officer-1",
        "officer-2",
    }


# ---------------------------------------------------------------------------
# Round trip through parse_digest_yaml
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc", [DOCUMENT_A, DOCUMENT_B], ids=["a", "b"])
def test_parse_returns_the_claims_that_were_put_in(stubbed_model, doc):
    d = digest_of(doc)
    parsed = parse_digest_yaml(d.text)
    got = {
        c["content"]: c
        for c in parsed["domain_claims"] + parsed["infrastructure_claims"]
    }
    emitted = canned.RESPONSES[(doc.key, "claims")]["claims"]
    assert set(got) == {c["content"] for c in emitted}

    for source in emitted:
        back = got[source["content"]]
        assert back["claim_type"] == source["claim_type"]
        assert back["original_excerpt"] == source["original_excerpt"]
        assert back["attribution_in_text"] == source["attribution_in_text"]
        assert back["speaker"] == source.get("speaker")
        assert back["date"] == source.get("date")
        assert back["node_references"] == [r["name"] for r in source["node_references"]]
        assert back["ref_roles"] == {
            r["name"]: r["role"] for r in source["node_references"]
        }
        chain = source["provenance_chain"]
        assert back["provenance_chain"]["origin_kind"] == chain["origin_kind"]
        if chain.get("origin_ref"):
            assert back["provenance_chain"]["origin_ref"] == chain["origin_ref"]


def test_parse_returns_the_nodes_and_terminology_that_were_put_in(stubbed_model):
    d = digest_of(DOCUMENT_A)
    parsed = parse_digest_yaml(d.text)
    source = canned.RESPONSES[(DOCUMENT_A.key, "nodes")]
    assert [(n["name"], n["node_type"]) for n in parsed["nodes"]] == [
        (n["name"], n["node_type"]) for n in source["nodes"]
    ]
    with_metadata = [n for n in parsed["nodes"] if n["metadata"]]
    assert [n["metadata"] for n in with_metadata] == [
        n["metadata"] for n in source["nodes"] if n.get("metadata")
    ]
    assert parsed["terminology"]["main_subject"] == source["main_subject"]


def test_parse_returns_the_record_block_whole(stubbed_model):
    d = digest_of(DOCUMENT_B, **FULL_YAML_KWARGS)
    parsed = parse_digest_yaml(d.text)
    assert parsed["frontmatter"]["record"] == d.doc["record"]
    assert parsed["frontmatter"]["record_id"] == FULL_YAML_KWARGS["record_id"]
    assert (
        parsed["frontmatter"]["content_hash"]
        == (FULL_YAML_KWARGS["record_content_hash"])
    )
    assert parsed["frontmatter"]["pre_digest"] == FULL_YAML_KWARGS["pre_digest"]
    assert parsed["frontmatter"]["ai_usage"] == FULL_YAML_KWARGS["ai_usage"]


# --- what the round trip drops, named one by one so no drop is silent --------


def test_the_claim_category_is_recoverable_only_from_which_list_it_landed_in(
    stubbed_model,
):
    d = digest_of(DOCUMENT_B)
    parsed = parse_digest_yaml(d.text)
    assert all("category" not in c for c in parsed["domain_claims"])
    assert {c["content"] for c in parsed["infrastructure_claims"]} == {
        c["content"]
        for c in canned.RESPONSES[(DOCUMENT_B.key, "claims")]["claims"]
        if c["category"] == "infrastructure"
    }


def test_an_empty_relay_is_dropped_at_emission_and_the_loss_is_harmless(stubbed_model):
    # _omit_empty drops relay: [], so a chain that reaches the digest with no
    # relay key is indistinguishable from one that never carried the field. That
    # is only safe because the model defaults it back to empty, and the
    # attestation derivation then reads the default. Proven, not assumed.
    from anomalica_common.digest.models import ProvenanceChain

    d = digest_of(DOCUMENT_A)
    chain = d.by_text("the plate solution placed at")["provenance_chain"]
    assert "relay" not in chain
    assert ProvenanceChain(**chain).relay == []
    assert ProvenanceChain(**chain).attestation().value == "first_hand"


def test_the_model_confidence_score_is_dropped_at_emission(stubbed_model):
    # The claims schema offers `confidence` and every canned claim carries one;
    # the writer never emits it, so nothing downstream can read it. Stated here
    # rather than discovered by someone looking for it.
    assert (
        "confidence"
        in extract.build_claims_schema_v2(NODE_NAMES_A)["properties"]["claims"][
            "items"
        ]["properties"]
    )
    assert all(
        "confidence" in c
        for c in canned.RESPONSES[(DOCUMENT_A.key, "claims")]["claims"]
    )
    d = digest_of(DOCUMENT_A)
    assert all("confidence" not in c for c in d.claims)
    assert all(
        c.get("confidence") is None for c in parse_digest_yaml(d.text)["domain_claims"]
    )


def test_a_claim_with_no_speaker_parses_back_with_no_speaker(stubbed_model):
    source = canned_claims(DOCUMENT_A, "remain unexplained")
    assert source["speaker"] is None
    d = digest_of(DOCUMENT_A)
    assert "speaker" not in d.by_text("remain unexplained")
    parsed = parse_digest_yaml(d.text)
    back = [c for c in parsed["domain_claims"] if c["content"] == source["content"]]
    assert back[0]["speaker"] is None
