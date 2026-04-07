from digester.markdown_format import extraction_to_markdown, parse_extraction_markdown
from digester.models import (
    AttestationLevel,
    ClaimType,
    ExtractionResult,
    ExtractedClaim,
    ExtractedNode,
    NodeType,
)


def _make_result():
    return ExtractionResult(
        record_title="Test Record",
        record_date="2004-11-14",
        record_reference="https://example.com",
        record_producer="Alice",
        nodes=[
            ExtractedNode(name="Alice", node_type=NodeType.person),
            ExtractedNode(name="ACME Corp", node_type=NodeType.organisation),
        ],
        claims=[
            ExtractedClaim(
                content="Alice works for ACME Corp.",
                original_excerpt="Alice works at ACME",
                claim_type=ClaimType.administrative,
                attestation=AttestationLevel.first_hand,
                speaker="Alice",
                location_in_record="paragraph 1",
                node_references=["Alice", "ACME Corp"],
            ),
            ExtractedClaim(
                content="ACME Corp was founded in 2001.",
                claim_type=ClaimType.administrative,
                attestation=AttestationLevel.second_hand,
                date="2001",
                node_references=["ACME Corp"],
            ),
        ],
    )


def test_roundtrip():
    """Serialise to markdown and parse back, check nothing is lost."""
    result = _make_result()
    md = extraction_to_markdown(result, model="test")
    parsed = parse_extraction_markdown(md)

    assert parsed["frontmatter"]["record_title"] == "Test Record"
    assert parsed["frontmatter"]["record_date"] == "2004-11-14"
    assert parsed["frontmatter"]["model"] == "test"

    assert len(parsed["nodes"]) == 2
    assert parsed["nodes"][0]["name"] == "Alice"
    assert parsed["nodes"][0]["node_type"] == "person"
    assert parsed["nodes"][1]["name"] == "ACME Corp"
    assert parsed["nodes"][1]["node_type"] == "organisation"

    assert len(parsed["domain_claims"]) == 2
    c1 = parsed["domain_claims"][0]
    assert c1["content"] == "Alice works for ACME Corp."
    assert c1["original_excerpt"] == "Alice works at ACME"
    assert c1["claim_type"] == "administrative"
    assert c1["attestation"] == "first_hand"
    assert c1["speaker"] == "Alice"
    assert c1["location_in_record"] == "paragraph 1"
    assert c1["node_references"] == ["Alice", "ACME Corp"]

    c2 = parsed["domain_claims"][1]
    assert c2["date"] == "2001"


def test_infrastructure_section():
    domain = _make_result()
    infra = ExtractionResult(
        record_title="Test Record",
        nodes=[],
        claims=[
            ExtractedClaim(
                content="Alice hosts the podcast.",
                claim_type=ClaimType.administrative,
                attestation=AttestationLevel.first_hand,
                node_references=["Alice"],
            ),
        ],
    )
    md = extraction_to_markdown(domain, infra_result=infra)
    parsed = parse_extraction_markdown(md)

    assert len(parsed["domain_claims"]) == 2
    assert len(parsed["infrastructure_claims"]) == 1
    assert parsed["infrastructure_claims"][0]["content"] == "Alice hosts the podcast."


def test_uuids_preserved():
    result = _make_result()
    md = extraction_to_markdown(result)
    parsed = parse_extraction_markdown(md)

    for node in parsed["nodes"]:
        assert len(node["id"]) == 36  # UUID format

    for claim in parsed["domain_claims"]:
        assert len(claim["id"]) == 36


def test_date_range():
    result = ExtractionResult(
        record_title="Test",
        nodes=[],
        claims=[
            ExtractedClaim(
                content="The programme ran from 2007 to 2012.",
                claim_type=ClaimType.administrative,
                attestation=AttestationLevel.first_hand,
                date="2007",
                date_end="2012",
            ),
        ],
    )
    md = extraction_to_markdown(result)
    parsed = parse_extraction_markdown(md)

    c = parsed["domain_claims"][0]
    assert c["date"] == "2007"
    assert c["date_end"] == "2012"
