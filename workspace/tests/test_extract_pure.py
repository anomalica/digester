"""Pure (non-LLM) logic in extract.py: response parsing/sanitising, the claims
schema's node-name enum, and document chunking. These are the deterministic,
breakable parts between the model and the data model."""

import json

from digester.extract import (
    CHUNK_MAX_CHARS,
    _build_chunks,
    _chunk_text,
    _format_directory_v2,
    _parse_response,
    build_claims_schema_v2,
    strip_word_timestamps,
)


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
    assert items["enum"] == names  # claims can only reference locked node names


def test_schema_without_names_has_no_enum():
    items = build_claims_schema_v2([])["properties"]["claims"]["items"]["properties"][
        "node_references"
    ]["items"]
    assert "enum" not in items
    assert items["type"] == "string"


def test_schema_requires_core_claim_fields():
    req = build_claims_schema_v2(["X"])["properties"]["claims"]["items"]["required"]
    assert set(req) == {"content", "category", "claim_type"}


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
