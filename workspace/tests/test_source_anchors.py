from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from anomalica_common.identity import record_identity
from anomalica_common.pre_digest import prepare_page_record

from digester import cli, health
from digester.authority import AuthorityError, validate_output
from digester.extract import _claim_chunk_spans, build_claims_schema_v2
from digester.record_parser import parse_record
from digester.source_anchors import (
    AnchorAlignmentError,
    anchor_claims,
    align_claim_source_anchors,
    digest_record_snapshot,
    is_digest2_record,
    record3_structure,
)

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64


def _frontmatter() -> dict:
    selection = [
        {"asset_hash": A, "selector": {"type": "pdf_page", "page": 2}},
        {"asset_hash": B, "selector": {"type": "whole"}},
    ]
    return {
        "schema": "anomalica/record/3",
        "content_hash": record_identity(selection),
        "title": "Mapped composite",
        "source_types": ["pdf", "image"],
        "assets": [
            {
                "asset_hash": A,
                "file_format": "pdf",
                "archived_ext": "pdf",
                "source_type": "pdf",
                "pages": 3,
                "acquisition": {"acquired_at": "2026-09-22T09:00:00Z"},
                "copyright": {"status": "licensed"},
            },
            {
                "asset_hash": B,
                "file_format": "png",
                "archived_ext": "png",
                "source_type": "image",
                "pages": 1,
                "acquisition": {"acquired_at": "2026-09-22T09:01:00Z"},
                "copyright": {"status": "public_domain"},
            },
        ],
        "selection": selection,
        "page_map": [
            {"record_page": 1, "asset_hash": A, "asset_file_page": 2},
            {"record_page": 2, "asset_hash": B, "asset_file_page": 1},
        ],
        "provenance": {
            "creators": ["Example Author"],
            "publisher": "Example Publisher",
            "published_date": "2024",
            "source_url": "https://example.invalid/work",
        },
        "work_provenance": {
            "root_id": "work:example",
            "evidence": ["title page"],
        },
    }


BODY = (
    "<!-- file_page: 1 -->\n"
    "Opening α duplicate evidence.\n"
    "First fragment near boundary.\n"
    "<!-- file_page: 2 -->\n"
    "Second fragment 🙂 closes claim.\n"
    "Opening α duplicate evidence.\n"
)


def _record_text(frontmatter: dict | None = None, body: str = BODY) -> str:
    return (
        "---\n"
        + yaml.safe_dump(frontmatter or _frontmatter(), sort_keys=False)
        + "---\n"
        + body
    )


def _prepared():
    record = parse_record(_record_text())
    structure = record3_structure(record)
    assert structure is not None
    return record, structure, prepare_page_record(structure, record.body)


def test_record3_parser_retains_shared_structure_inputs_and_nested_provenance():
    record = parse_record(_record_text())
    structure = record3_structure(record)
    assert structure is not None
    assert is_digest2_record(structure)
    assert [page.file_page for page in record.pages] == [1, 2]
    assert record.creators == ["Example Author"]
    assert record.publisher == "Example Publisher"
    assert record.date == "2024"
    assert record.reference == "https://example.invalid/work"
    assert structure.page_map[0].asset_file_page == 2


def test_page_mapped_response_requires_quotes_and_claim_chunks_overlap():
    item = build_claims_schema_v2(["Example Author"], require_original_excerpt=True)[
        "properties"
    ]["claims"]["items"]
    assert "original_excerpt" in item["required"]

    text = "a" * 20_000 + "\n<!-- file_page: 2 -->\n" + "b" * 20_000
    chunks = _claim_chunk_spans(text, max_chars=25_000, overlap_chars=8)
    assert len(chunks) == 2
    first_start, first_end, first = chunks[0]
    second_start, second_end, second = chunks[1]
    assert first_start == 0 and second_end == len(text)
    assert first_end - second_start == 16
    assert first[-16:] == second[:16]


def test_multipart_quote_maps_across_pages_assets_and_unicode_code_points():
    _, _, prepared = _prepared()
    claim = {
        "original_excerpt": (
            "First fragment near boundary....Second fragment 🙂 closes claim."
        ),
        "location_in_record": "file_page: 1",
    }
    anchors = align_claim_source_anchors(claim, prepared).root

    assert [anchor.record_page for anchor in anchors] == [1, 2]
    assert [anchor.asset_hash for anchor in anchors] == [A, B]
    assert [anchor.asset_file_page for anchor in anchors] == [2, 1]
    assert [anchor.quote for anchor in anchors] == [
        "First fragment near boundary.",
        "Second fragment 🙂 closes claim.",
    ]
    for anchor in anchors:
        assert prepared.text[anchor.body_span.start : anchor.body_span.end] == (
            anchor.quote
        )
        asset_text = prepared.asset_texts[
            (anchor.asset_hash, anchor.asset_file_page, anchor.asset_text_sha256)
        ]
        assert asset_text[anchor.asset_span.start : anchor.asset_span.end] == (
            anchor.quote
        )


def test_ambiguous_exact_quote_fails_closed_but_record_page_hint_disambiguates():
    _, _, prepared = _prepared()
    with pytest.raises(AnchorAlignmentError, match="ambiguous"):
        align_claim_source_anchors(
            {"original_excerpt": "Opening α duplicate evidence."}, prepared
        )

    anchor = align_claim_source_anchors(
        {
            "original_excerpt": "Opening α duplicate evidence.",
            "location_in_record": "record page 2",
        },
        prepared,
    ).root[0]
    assert anchor.record_page == 2
    assert anchor.asset_hash == B


def test_realigner_does_not_repair_a_non_verbatim_quote():
    _, _, prepared = _prepared()

    with pytest.raises(AnchorAlignmentError, match="absent"):
        align_claim_source_anchors(
            {
                "original_excerpt": " First fragment near boundary.",
                "location_in_record": "record page 1",
            },
            prepared,
        )


def test_elided_fragments_remain_separate_and_missing_mapping_fails_closed():
    _, _, prepared = _prepared()
    anchors = align_claim_source_anchors(
        {
            "original_excerpt": (
                "Opening α duplicate evidence....First fragment near boundary."
            ),
            "location_in_record": "file_page 1",
        },
        prepared,
    ).root
    assert len(anchors) == 2
    assert anchors[0].asset_span.end <= anchors[1].asset_span.start

    image_body = BODY.replace(
        "First fragment near boundary.", "<!-- image: described diagram -->"
    )
    record = parse_record(_record_text(body=image_body))
    structure = record3_structure(record)
    prepared_image = prepare_page_record(structure, record.body)
    with pytest.raises(AnchorAlignmentError, match="synthetic|mapping"):
        align_claim_source_anchors(
            {"original_excerpt": "[image: described diagram]"}, prepared_image
        )


def test_equal_claim_evidence_is_unioned_in_canonical_order():
    _, _, prepared = _prepared()
    claims, rejected = anchor_claims(
        [
            {
                "content": "The repeated proposition has evidence on both pages.",
                "provenance_chain": {
                    "origin_kind": "document",
                    "origin": "Mapped composite",
                    "relay": [],
                },
                "original_excerpt": "First fragment near boundary.",
                "location_in_record": "file_page 1",
                "_additional_evidence": [
                    {
                        "original_excerpt": "Second fragment 🙂 closes claim.",
                        "location_in_record": "file_page 2",
                    }
                ],
            }
        ],
        prepared,
    )
    assert rejected == []
    assert [anchor["record_page"] for anchor in claims[0]["source_anchors"]] == [
        1,
        2,
    ]


def test_do_extract_emits_and_validates_digest2_without_a_provider(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DIGESTER_ENTAILMENT", "off")
    record_path = tmp_path / "record.md"
    record_path.write_text(_record_text())
    parsed = parse_record(record_path.read_text())
    output = tmp_path / "digest.yaml"
    predigests = tmp_path / "derived" / "pre-digests"

    import digester.extract as extract

    result = {
        "nodes": [],
        "claims": [
            {
                "content": "The two selected pages jointly state the example.",
                "category": "domain",
                "claim_type": "administrative",
                "provenance_chain": {
                    "origin_kind": "document",
                    "origin": "Mapped composite",
                    "relay": [],
                },
                "attribution_in_text": True,
                "original_excerpt": (
                    "First fragment near boundary....Second fragment 🙂 closes claim."
                ),
                "location_in_record": "file_page 1 to file_page 2",
                "node_references": [],
            }
        ],
        "main_subject": "",
        "codenames_to_resolve": [],
        "acronyms": [],
        "prompt_provenance": extract.prompt_provenance(),
    }
    monkeypatch.setattr(extract, "extract_two_pass", lambda *args, **kwargs: result)

    cli._do_extract(
        record_path,
        parsed,
        output,
        "haiku",
        False,
        None,
        False,
        predigests,
        None,
        input_authority=object(),
    )

    digest = yaml.safe_load(output.read_text())
    assert digest["schema"] == "anomalica/digest/2"
    assert digest["pre_digest"]["prep_version"] == 9
    assert digest["pre_digest"]["sha256"].startswith("sha256:")
    assert digest["pre_digest"]["source_map_sha256"].startswith("sha256:")
    assert digest["record_snapshot_sha256"].startswith("sha256:")
    assert digest["record"]["selection"] == _frontmatter()["selection"]
    assert digest["record"]["publisher"] == "Example Publisher"
    assert digest["record"]["date"] == "2024"
    assert [item["status"] for item in digest["record"]["asset_rights"]] == [
        "licensed",
        "public_domain",
    ]
    claim = digest["domain_claims"][0]
    assert "location" not in claim
    assert [anchor["record_page"] for anchor in claim["source_anchors"]] == [1, 2]
    assert all(anchor["quote"] for anchor in claim["source_anchors"])

    source_map_path = (
        predigests.parent
        / "source-maps"
        / f"{digest['pre_digest']['source_map_sha256'].removeprefix('sha256:')}.json"
    )
    assert source_map_path.exists()
    source_map = json.loads(source_map_path.read_text())
    assert source_map["record_hash"] == _frontmatter()["content_hash"]
    pointer = json.loads(
        (
            predigests
            / "by-record"
            / f"{_frontmatter()['content_hash'].removeprefix('sha256:')}.json"
        ).read_text()
    )
    assert pointer["prep_version"] == 9

    snapshot = digest_record_snapshot(parsed, record3_structure(parsed))
    assert snapshot.content_hash == digest["record"]["content_hash"]
    validated = validate_output(tmp_path, output, record_path)
    assert validated["record_content_hash"] == _frontmatter()["content_hash"]

    monkeypatch.setattr(health, "_SURVIVAL_CACHE", tmp_path / "health-cache.json")
    store = tmp_path / "ingests" / "store"
    by_name = tmp_path / "ingests" / "by-name"
    store.mkdir(parents=True)
    by_name.mkdir()
    stored_record = (
        store / f"{_frontmatter()['content_hash'].removeprefix('sha256:')}.md"
    )
    stored_record.write_text(_record_text())
    (by_name / "mapped-composite.md").symlink_to(f"../store/{stored_record.name}")
    freshness = health.pre_digest_input_freshness(
        tmp_path, by_name, loaded=[("mapped-composite", digest)]
    )
    assert [item["digest"] for item in freshness["current"]] == ["mapped-composite"]

    wrong_prep = copy.deepcopy(digest)
    wrong_prep["pre_digest"]["prep_version"] = 8
    freshness = health.pre_digest_input_freshness(
        tmp_path, by_name, loaded=[("mapped-composite", wrong_prep)]
    )
    assert [item["issue"] for item in freshness["stale"]] == ["prep_version"]

    tampered = copy.deepcopy(digest)
    tampered["record"]["asset_rights"][0]["status"] = "unknown"
    output.write_text(yaml.safe_dump(tampered, sort_keys=False))
    with pytest.raises(AuthorityError, match="Record projection"):
        validate_output(tmp_path, output, record_path)

    stale_map = copy.deepcopy(digest)
    stale_map["pre_digest"]["source_map_sha256"] = "sha256:" + "f" * 64
    output.write_text(yaml.safe_dump(stale_map, sort_keys=False))
    with pytest.raises(AuthorityError, match="source_map_sha256"):
        validate_output(tmp_path, output, record_path)

    stale_anchor = copy.deepcopy(digest)
    stale_anchor["domain_claims"][0]["source_anchors"][0]["asset_text_sha256"] = (
        "sha256:" + "f" * 64
    )
    output.write_text(yaml.safe_dump(stale_anchor, sort_keys=False))
    with pytest.raises(AuthorityError, match="source anchor disagrees"):
        validate_output(tmp_path, output, record_path)

    output.write_text(yaml.safe_dump(digest, sort_keys=False))
    changed_frontmatter = _frontmatter()
    changed_frontmatter["title"] = "Changed title"
    changed_record = _record_text(changed_frontmatter)
    record_path.write_text(changed_record)
    stored_record.write_text(changed_record)
    with pytest.raises(AuthorityError, match="snapshot is not current"):
        validate_output(tmp_path, output, record_path)
    freshness = health.pre_digest_input_freshness(
        tmp_path, by_name, loaded=[("mapped-composite", digest)]
    )
    assert [item["issue"] for item in freshness["stale"]] == ["record_changed"]


def test_non_paged_record3_remains_digest1_with_a_record_snapshot(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DIGESTER_ENTAILMENT", "off")
    selection = [{"asset_hash": A, "selector": {"type": "whole"}}]
    frontmatter = {
        "schema": "anomalica/record/3",
        "content_hash": record_identity(selection),
        "title": "Non-paged web record",
        "assets": [
            {
                "asset_hash": A,
                "file_format": "html",
                "archived_ext": "html",
                "source_type": "web",
                "acquisition": {"acquired_at": "2026-09-22T09:00:00Z"},
                "copyright": {"status": "publicly_accessible"},
            }
        ],
        "selection": selection,
    }
    record_path = tmp_path / "web.md"
    record_path.write_text(_record_text(frontmatter, "A web claim."))
    parsed = parse_record(record_path.read_text())
    output = tmp_path / "web.yaml"

    import digester.extract as extract

    monkeypatch.setattr(
        extract,
        "extract_two_pass",
        lambda *args, **kwargs: {
            "nodes": [],
            "claims": [],
            "main_subject": "",
            "codenames_to_resolve": [],
            "acronyms": [],
            "prompt_provenance": extract.prompt_provenance(),
        },
    )
    cli._do_extract(
        record_path,
        parsed,
        output,
        "haiku",
        input_authority=object(),
    )

    digest = yaml.safe_load(output.read_text())
    assert digest["schema"] == "anomalica/digest/1"
    assert digest["pre_digest"]["prep_version"] == 9
    assert not digest["pre_digest"]["sha256"].startswith("sha256:")
    assert "source_map_sha256" not in digest["pre_digest"]
    assert digest["record_snapshot_sha256"].startswith("sha256:")
    assert digest["record"]["assets"][0]["source_type"] == "web"
    validate_output(tmp_path, output, record_path)

    stale_projection = copy.deepcopy(digest)
    stale_projection["record"]["work_provenance"] = {
        "root_id": "invented",
        "evidence": ["not in the Record"],
    }
    output.write_text(yaml.safe_dump(stale_projection, sort_keys=False))
    with pytest.raises(AuthorityError, match="Record projection"):
        validate_output(tmp_path, output, record_path)

    exact_anchor_on_digest1 = copy.deepcopy(digest)
    exact_anchor_on_digest1["domain_claims"] = [{"source_anchors": [{"fake": True}]}]
    output.write_text(yaml.safe_dump(exact_anchor_on_digest1, sort_keys=False))
    with pytest.raises(AuthorityError, match="cannot carry exact source anchors"):
        validate_output(tmp_path, output, record_path)


def test_legacy_page_markers_remain_digest1_without_exact_anchors(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DIGESTER_ENTAILMENT", "off")
    record_path = tmp_path / "legacy.md"
    record_path.write_text(
        "---\n"
        "schema: anomalica/record/1\n"
        f"content_hash: {A}\n"
        "title: Legacy PDF\n"
        "source_type: pdf\n"
        "---\n"
        "<!-- file_page: 1 -->\nLegacy page text.\n"
    )
    parsed = parse_record(record_path.read_text())
    output = tmp_path / "legacy.yaml"

    import digester.extract as extract

    monkeypatch.setattr(
        extract,
        "extract_two_pass",
        lambda *args, **kwargs: {
            "nodes": [],
            "claims": [],
            "main_subject": "",
            "codenames_to_resolve": [],
            "acronyms": [],
            "prompt_provenance": extract.prompt_provenance(),
        },
    )
    cli._do_extract(
        record_path,
        parsed,
        output,
        "haiku",
        input_authority=object(),
    )

    digest = yaml.safe_load(output.read_text())
    assert digest["schema"] == "anomalica/digest/1"
    assert digest["pre_digest"]["prep_version"] == 9
    assert "source_map_sha256" not in digest["pre_digest"]
    assert "record_snapshot_sha256" not in digest
    validate_output(tmp_path, output, record_path)


def test_page_mapped_extraction_requires_persistent_source_map_before_model(
    tmp_path: Path, monkeypatch
):
    record_path = tmp_path / "record.md"
    record_path.write_text(_record_text())
    parsed = parse_record(record_path.read_text())
    calls = []
    import digester.extract as extract

    monkeypatch.setattr(
        extract, "extract_two_pass", lambda *args, **kwargs: calls.append(True)
    )
    with pytest.raises(Exception, match="requires --predigests-root"):
        cli._do_extract(
            record_path,
            parsed,
            tmp_path / "digest.yaml",
            "haiku",
            input_authority=object(),
        )
    assert calls == []
