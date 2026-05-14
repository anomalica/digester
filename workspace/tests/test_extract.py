import pytest

from digester.extract import (
    DOMAIN_SCHEMA,
    INFRASTRUCTURE_SCHEMA,
    _chunk_text,
    _extraction_schema,
    _find_split_point,
    _parse_json,
)


def test_parse_json_strips_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_tolerates_trailing_commentary():
    # This is the failure mode we hit on the first batch run - Claude would
    # emit valid JSON then add a sentence. The old find/rfind approach broke
    # when the trailing text contained a brace.
    raw = '{"a": 1, "b": 2}\n\nNote: extracted from page 3.'
    assert _parse_json(raw) == {"a": 1, "b": 2}


def test_parse_json_tolerates_leading_preamble():
    raw = 'Here is the extraction:\n\n{"a": 1}'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_empty_raises():
    with pytest.raises(ValueError, match="Empty response"):
        _parse_json("")
    with pytest.raises(ValueError, match="Empty response"):
        _parse_json("   \n  ")


def test_parse_json_no_object_raises():
    with pytest.raises(ValueError, match="No JSON object"):
        _parse_json("nothing structured here")


def test_parse_json_invalid_raises():
    with pytest.raises(ValueError, match="Invalid JSON"):
        _parse_json('{"a": 1, "b":}')


def test_extraction_schema_required_fields():
    schema = _extraction_schema(["person", "organisation"])
    assert "record_title" in schema["required"]
    assert "nodes" in schema["required"]
    assert "claims" in schema["required"]
    node_enum = schema["properties"]["nodes"]["items"]["properties"]["node_type"][
        "enum"
    ]
    assert node_enum == ["person", "organisation"]


def test_domain_schema_excludes_record_node_type():
    node_enum = DOMAIN_SCHEMA["properties"]["nodes"]["items"]["properties"][
        "node_type"
    ]["enum"]
    assert "record" not in node_enum
    assert "person" in node_enum


def test_infrastructure_schema_includes_record_node_type():
    node_enum = INFRASTRUCTURE_SCHEMA["properties"]["nodes"]["items"]["properties"][
        "node_type"
    ]["enum"]
    assert "record" in node_enum
    assert "person" in node_enum


def test_claim_type_and_attestation_enums_are_constrained():
    claim_schema = DOMAIN_SCHEMA["properties"]["claims"]["items"]
    assert "first_hand" in claim_schema["properties"]["attestation"]["enum"]
    assert "observation" in claim_schema["properties"]["claim_type"]["enum"]


def test_chunk_text_short_returns_single_chunk():
    text = "short text\n\n" * 100  # 1200 chars, well below default max
    assert _chunk_text(text) == [text]


def test_chunk_text_splits_long_input():
    paragraph = "Lorem ipsum dolor sit amet. " * 200  # ~5600 chars
    text = "\n\n".join([paragraph] * 20)  # ~112,000 chars
    chunks = _chunk_text(text, max_chars=50_000, min_chars=20_000)
    assert len(chunks) >= 2
    # No chunk exceeds the cap
    assert all(len(c) <= 50_000 for c in chunks)
    # Reassembly is faithful
    assert "".join(chunks) == text


def test_chunk_text_prefers_page_markers():
    text = "body text " * 3000  # 30,000 chars
    text += "\n<!-- file_page: 5 -->\n"
    text += "more body " * 3000  # 30,000 chars
    chunks = _chunk_text(text, max_chars=50_000, min_chars=20_000)
    assert len(chunks) == 2
    # The split should land on the page-marker line so the marker starts chunk 2.
    assert chunks[1].startswith("<!-- file_page:")


def test_chunk_text_prefers_headings_when_no_page_markers():
    body = "Paragraph sentence here. " * 1000  # 25,000 chars
    text = body + "\n## Chapter 2\n" + body
    chunks = _chunk_text(text, max_chars=40_000, min_chars=15_000)
    assert len(chunks) == 2
    assert chunks[1].startswith("## Chapter 2")


def test_find_split_point_no_boundary_returns_none():
    text = "a" * 200_000  # no natural boundaries inside the window
    assert _find_split_point(text, 30_000, 60_000) is None


def test_chunk_text_falls_back_to_hard_cap_when_no_boundaries():
    text = "a" * 200_000
    chunks = _chunk_text(text, max_chars=50_000, min_chars=20_000)
    assert len(chunks) == 4
    assert all(len(c) <= 50_000 for c in chunks)
    assert "".join(chunks) == text
