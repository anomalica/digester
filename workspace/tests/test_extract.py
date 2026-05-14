import pytest

from digester.extract import (
    DOMAIN_SCHEMA,
    INFRASTRUCTURE_SCHEMA,
    _extraction_schema,
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
