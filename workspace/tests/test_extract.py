import pytest

from digester.extract import (
    DOMAIN_SCHEMA,
    INFRASTRUCTURE_SCHEMA,
    _build_chunks,
    _chunk_text,
    _extraction_schema,
    _find_split_point,
    _format_exclude_list,
    _parse_json,
    _split_at_chapters,
)
from anomalica_common.digest import AttestationLevel, ClaimType, ExtractedClaim


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


def test_concept_is_a_first_class_node_type():
    # decision 0025: concept is an ingestion type, in both schemas
    from anomalica_common.digest import NodeType

    assert NodeType.concept.value == "concept"
    assert (
        "concept"
        in DOMAIN_SCHEMA["properties"]["nodes"]["items"]["properties"]["node_type"][
            "enum"
        ]
    )
    assert (
        "concept"
        in INFRASTRUCTURE_SCHEMA["properties"]["nodes"]["items"]["properties"][
            "node_type"
        ]["enum"]
    )


def test_infrastructure_schema_includes_record_node_type():
    node_enum = INFRASTRUCTURE_SCHEMA["properties"]["nodes"]["items"]["properties"][
        "node_type"
    ]["enum"]
    assert "record" in node_enum
    assert "person" in node_enum


def test_build_record_context_pins_author():
    from digester.extract import build_record_context

    ctx = build_record_context(
        title="In Plain Sight",
        authors=["Ross Coulthart"],
        date="2023",
        source_type="ebook",
    )
    assert "In Plain Sight" in ctx
    assert "ebook" in ctx
    assert "Ross Coulthart" in ctx
    assert "the author" in ctx.lower()
    assert "never emit" in ctx.lower()
    assert ctx.endswith("\n\n")


def test_build_record_context_no_authors_omits_pin():
    from digester.extract import build_record_context

    ctx = build_record_context(
        title="FOIA Release 18-F-0324",
        authors=[],
        date=None,
        source_type="pdf",
    )
    assert "FOIA Release 18-F-0324" in ctx
    # No author -> no first-person pinning instruction
    assert "first person" not in ctx.lower()
    assert "SOURCE RECORD:" in ctx


def test_build_record_context_multiple_authors():
    from digester.extract import build_record_context

    ctx = build_record_context(
        title="Some Article",
        authors=["Helene Cooper", "Ralph Blumenthal", "Leslie Kean"],
        date="2017-12-16",
        source_type="web",
    )
    assert "Helene Cooper, Ralph Blumenthal, Leslie Kean" in ctx


def test_parse_response_surfaces_extraction_complete():
    from digester.extract import _parse_response

    raw_done = (
        '{"record_title": "T", "nodes": [], "claims": [], "extraction_complete": true}'
    )
    assert _parse_response(raw_done).extraction_complete is True

    raw_not = '{"record_title": "T", "nodes": [], "claims": []}'
    # Absent field defaults to False (assume more to extract)
    assert _parse_response(raw_not).extraction_complete is False

    raw_false = (
        '{"record_title": "T", "nodes": [], "claims": [], "extraction_complete": false}'
    )
    assert _parse_response(raw_false).extraction_complete is False


def test_extraction_schema_allows_extraction_complete():
    schema = _extraction_schema(["person"])
    assert "extraction_complete" in schema["properties"]
    assert schema["properties"]["extraction_complete"]["type"] == "boolean"
    # It is optional - not in the required list (a model that omits it = not done)
    assert "extraction_complete" not in schema["required"]


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


def test_split_at_chapters_uses_record_format_markers():
    body = "Paragraph sentence here. " * 200
    text = (
        "<!-- chapter: 1 -->\n"
        + body
        + "\n<!-- chapter: 2 -->\n"
        + body
        + "\n<!-- chapter: 3 -->\n"
        + body
    )
    chapters = _split_at_chapters(text)
    assert chapters is not None
    assert len(chapters) == 3
    assert chapters[0].startswith("<!-- chapter: 1 -->")
    assert chapters[2].startswith("<!-- chapter: 3 -->")


def test_split_at_chapters_returns_none_without_chapter_markers():
    text = "## Just a heading\n" + ("Body text. " * 200)
    assert _split_at_chapters(text) is None


def test_split_at_chapters_returns_none_with_only_one_marker():
    # A single chapter marker (front matter only) is not a meaningful split.
    text = "<!-- chapter: 1 -->\nbody body body"
    assert _split_at_chapters(text) is None


def test_build_chunks_uses_chapter_markers_when_present():
    body = "Paragraph here. " * 2000  # ~32KB
    text = (
        "<!-- chapter: 1 -->\n"
        + body
        + "\n<!-- chapter: 2 -->\n"
        + body
        + "\n<!-- chapter: 3 -->\n"
        + body
    )
    chunks = _build_chunks(text)
    assert len(chunks) == 3
    assert all(c.startswith("<!-- chapter: ") for c in chunks)


def test_build_chunks_subsplits_oversized_chapter():
    # One huge chapter that exceeds the hard cap, plus a small one after.
    huge = "Paragraph break here.\n\n" * 10000  # ~230KB
    text = "<!-- chapter: 1 -->\n" + huge + "\n<!-- chapter: 2 -->\nshort body"
    chunks = _build_chunks(text)
    # The big chapter splits into multiple chunks; the small one stays as one.
    assert len(chunks) >= 3
    from digester.extract import CHUNK_HARD_MAX

    assert all(len(c) <= CHUNK_HARD_MAX for c in chunks)


def test_build_chunks_falls_back_to_windows_without_chapter_markers():
    text = "No chapter markers. " * 5000  # ~100KB
    chunks = _build_chunks(text)
    assert len(chunks) > 1


def test_format_exclude_list_renders_compactly():
    claims = [
        ExtractedClaim(
            content=f"Claim number {i}",
            claim_type=ClaimType.observation,
            attestation=AttestationLevel.first_hand,
        )
        for i in range(3)
    ]
    out = _format_exclude_list(claims)
    assert "1. Claim number 0" in out
    assert "2. Claim number 1" in out
    assert "3. Claim number 2" in out
