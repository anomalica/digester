"""Digest/2 preparation, Record projection and exact claim anchoring.

The canonical record, source-map, span and digest binding models live in
``anomalica_common``. This module owns only Digester policy: choosing digest/2
for eligible inputs, realigning model quotes and shaping the emitted envelope.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import yaml
from anomalica_common.digest import SourceAnchor, SourceAnchors, _yaml_dump
from anomalica_common.identity import digest_record_snapshot_identity
from anomalica_common.pre_digest import (
    PreparedPageRecord,
    SourceMapError,
    source_anchor_for_body_span,
    validate_digest2_bindings,
    validate_source_anchors,
)
from anomalica_common.records import (
    DigestRecordSnapshot,
    Record3Structure,
    SourceType,
    Span,
)

from digester.record_parser import ParsedRecord

_CHAR_LOCATION = re.compile(r"\bchar\s*:\s*(\d+)\s*-\s*(\d+)\b", re.IGNORECASE)
_RECORD_PAGE_LOCATION = re.compile(
    r"\b(?:file_page|record_page|record\s+page)\s*:?\s*(\d+)\b",
    re.IGNORECASE,
)


class AnchorAlignmentError(SourceMapError):
    """A model claim cannot be bound to one exact set of source intervals."""


def record3_structure(record: ParsedRecord) -> Record3Structure | None:
    """Return the canonical shared structure, validating every record/3 input."""
    if record.schema_version != "anomalica/record/3":
        return None
    return Record3Structure.from_frontmatter(record.frontmatter)


def is_digest2_record(structure: Record3Structure | None) -> bool:
    """Whether ADR 0051 gives this Record typed exact claim coordinates."""
    if structure is None or structure.page_map is None:
        return False
    return all(
        asset.source_type in (SourceType.pdf.value, SourceType.image.value)
        for asset in structure.assets
    )


def digest_record_snapshot(
    record: ParsedRecord, structure: Record3Structure
) -> DigestRecordSnapshot:
    """Project the exact graph-relevant mutable Record fields for its binding."""
    frontmatter = record.frontmatter
    assets = []
    asset_rights = []
    for asset in structure.assets:
        projected = {
            "asset_hash": asset.asset_hash,
            "source_type": asset.source_type,
            "file_format": asset.file_format,
        }
        if asset.pages is not None:
            projected["pages"] = asset.pages
        if asset.derived_from is not None:
            projected["derived_from"] = asset.derived_from.model_dump(mode="json")
        assets.append(projected)
        asset_rights.append(
            {"asset_hash": asset.asset_hash, "status": asset.copyright.status}
        )

    value = {
        "schema": "anomalica/digest-record-snapshot/1",
        "content_hash": structure.content_hash,
        "title": record.title,
        "assets": assets,
        "asset_rights": asset_rights,
        "selection": structure.selection.model_dump(mode="json"),
    }
    for key in ("provenance", "work_provenance"):
        if frontmatter.get(key) is not None:
            value[key] = frontmatter[key]
    if structure.page_map is not None:
        value["page_map"] = [
            page.model_dump(mode="json") for page in structure.page_map
        ]
    return DigestRecordSnapshot.model_validate(value)


def digest_record_extra(snapshot: DigestRecordSnapshot) -> dict:
    """Public structural fields copied into the digest's Record block."""
    value = snapshot.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {
        key: value[key]
        for key in (
            "provenance",
            "work_provenance",
            "assets",
            "asset_rights",
            "selection",
            "page_map",
        )
        if key in value
    }


def validate_digest_record_projection(
    record_block: object, snapshot: DigestRecordSnapshot
) -> None:
    """Require the digest's public Record projection to equal its hash input."""
    if not isinstance(record_block, Mapping):
        raise AnchorAlignmentError("digest Record block is not a mapping")
    expected = {
        "title": snapshot.title,
        "content_hash": snapshot.content_hash,
        **digest_record_extra(snapshot),
    }
    snapshot_fields = {
        "title",
        "content_hash",
        "provenance",
        "work_provenance",
        "assets",
        "asset_rights",
        "selection",
        "page_map",
    }
    actual = {key: record_block[key] for key in snapshot_fields if key in record_block}
    if actual != expected:
        missing = object()
        changed = sorted(
            key
            for key in snapshot_fields
            if actual.get(key, missing) != expected.get(key, missing)
        )
        raise AnchorAlignmentError(
            "digest Record projection disagrees with its snapshot: "
            + ", ".join(changed)
        )


def _occurrences(text: str, fragment: str, bounds: tuple[int, int] | None) -> list[int]:
    start, end = bounds or (0, len(text))
    positions: list[int] = []
    position = text.find(fragment, start, end)
    while position >= 0 and position + len(fragment) <= end:
        positions.append(position)
        position = text.find(fragment, position + 1, end)
    return positions


def _anchors_covering_span(
    prepared: PreparedPageRecord, start: int, end: int
) -> list[SourceAnchor]:
    """Map every code point in one exact fragment, splitting only at map gaps."""
    anchors: list[SourceAnchor] = []
    cursor = start
    entries = prepared.source_map.entries
    while cursor < end:
        entry = next(
            (
                candidate
                for candidate in entries
                if candidate.body_span.start <= cursor < candidate.body_span.end
            ),
            None,
        )
        if entry is None:
            raise AnchorAlignmentError(
                "quote touches synthetic text or a missing source mapping"
            )
        segment_end = min(end, entry.body_span.end)
        anchors.append(
            source_anchor_for_body_span(prepared, Span(start=cursor, end=segment_end))
        )
        cursor = segment_end
    return anchors


def _location_page(location: object) -> int | None:
    match = _RECORD_PAGE_LOCATION.search(str(location or ""))
    return int(match.group(1)) if match else None


def _location_span(location: object) -> tuple[int, int] | None:
    match = _CHAR_LOCATION.search(str(location or ""))
    if match is None:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    return (start, end) if start < end else None


def _page_for_occurrence(
    prepared: PreparedPageRecord, start: int, end: int
) -> set[int]:
    try:
        return {
            anchor.record_page
            for anchor in _anchors_covering_span(prepared, start, end)
        }
    except SourceMapError:
        return set()


def _ordered_alignments(
    candidates: Sequence[Sequence[int]], fragments: Sequence[str], limit: int = 2
) -> list[list[tuple[int, int]]]:
    """Enumerate only enough source-ordered alignments to prove ambiguity."""
    alignments: list[list[tuple[int, int]]] = []

    def visit(index: int, minimum: int, spans: list[tuple[int, int]]) -> None:
        if len(alignments) >= limit:
            return
        if index == len(fragments):
            alignments.append(list(spans))
            return
        length = len(fragments[index])
        for start in candidates[index]:
            if start < minimum:
                continue
            spans.append((start, start + length))
            visit(index + 1, start + length, spans)
            spans.pop()
            if len(alignments) >= limit:
                return

    visit(0, 0, [])
    return alignments


def _quote_fragments(quote: str) -> list[str]:
    """Split ASCII elisions without stealing sentence-final full stops.

    A source fragment ending in ``.`` followed immediately by the ``...`` join
    is four consecutive full stops. The join is the final three; any preceding
    full stops remain verbatim source characters.
    """
    parts: list[str] = []
    position = 0
    for match in re.finditer(r"\.{3,}", quote):
        marker_start = match.end() - 3
        parts.append(quote[position:marker_start])
        position = match.end()
    parts.append(quote[position:])
    if any(not part.strip() for part in parts):
        raise AnchorAlignmentError("an elision marker must join two quote fragments")
    return parts


def align_claim_source_anchors(
    claim: Mapping,
    prepared: PreparedPageRecord,
) -> SourceAnchors:
    """Realign one model quote exactly, rejecting missing or ambiguous evidence.

    ``...`` is the only elision marker. Its non-empty verbatim fragments remain
    distinct anchors even when their source intervals happen to touch.
    """
    quote = claim.get("original_excerpt")
    if not isinstance(quote, str) or not quote.strip():
        raise AnchorAlignmentError("digest/2 claims require a non-empty exact quote")
    fragments = _quote_fragments(quote)

    raw_bounds = claim.get("_source_chunk")
    bounds = None
    if isinstance(raw_bounds, Mapping):
        start, end = raw_bounds.get("start"), raw_bounds.get("end")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and 0 <= start < end <= len(prepared.text)
        ):
            bounds = (start, end)

    candidates = [
        _occurrences(prepared.text, fragment, bounds) for fragment in fragments
    ]
    if any(not positions for positions in candidates):
        raise AnchorAlignmentError(
            "an exact quote fragment is absent from the pre-digest"
        )

    page_hint = _location_page(claim.get("location_in_record"))
    if page_hint is not None:
        first_fragment = fragments[0]
        on_page = [
            start
            for start in candidates[0]
            if page_hint
            in _page_for_occurrence(prepared, start, start + len(first_fragment))
        ]
        if on_page:
            candidates[0] = on_page

    span_hint = _location_span(claim.get("location_in_record"))
    if span_hint is not None and len(candidates[0]) > 1:
        hint_start, hint_end = span_hint

        def distance(start: int) -> int:
            end = start + len(fragments[0])
            if start < hint_end and hint_start < end:
                return 0
            return min(abs(start - hint_start), abs(end - hint_end))

        nearest = min(distance(start) for start in candidates[0])
        candidates[0] = [start for start in candidates[0] if distance(start) == nearest]

    alignments = _ordered_alignments(candidates, fragments)
    if not alignments:
        raise AnchorAlignmentError("exact quote fragments are not in source order")
    if len(alignments) != 1:
        raise AnchorAlignmentError("exact quote alignment is ambiguous")

    anchors: list[SourceAnchor] = []
    # Deliberately process each elided fragment separately. Never coalesce across
    # a join, even if the two source intervals touch.
    for start, end in alignments[0]:
        anchors.extend(_anchors_covering_span(prepared, start, end))
    anchors.sort(
        key=lambda anchor: (
            anchor.record_page,
            anchor.asset_span.start,
            anchor.asset_span.end,
        )
    )
    validated = SourceAnchors(anchors)
    return validate_source_anchors(validated, prepared)


def anchor_claims(
    claims: Sequence[dict], prepared: PreparedPageRecord
) -> tuple[list[dict], list[dict]]:
    """Return valid digest/2 claims and closed failure diagnostics."""
    anchored: list[dict] = []
    rejected: list[dict] = []
    for claim in claims:
        try:
            if not isinstance(claim.get("provenance_chain"), Mapping) or not claim.get(
                "provenance_chain"
            ):
                raise AnchorAlignmentError("digest/2 claims require a provenance chain")
            evidence = [claim]
            additional = claim.get("_additional_evidence")
            if isinstance(additional, Sequence) and not isinstance(
                additional, (str, bytes)
            ):
                evidence.extend(
                    item for item in additional if isinstance(item, Mapping)
                )
            combined = [
                anchor
                for item in evidence
                for anchor in align_claim_source_anchors(item, prepared).root
            ]
            unique = {
                (
                    anchor.asset_hash,
                    anchor.record_page,
                    anchor.asset_file_page,
                    anchor.asset_text_sha256,
                    anchor.asset_span.start,
                    anchor.asset_span.end,
                    anchor.body_span.start,
                    anchor.body_span.end,
                    anchor.quote,
                ): anchor
                for anchor in combined
            }
            anchors = SourceAnchors(
                sorted(
                    unique.values(),
                    key=lambda anchor: (
                        anchor.record_page,
                        anchor.asset_span.start,
                        anchor.asset_span.end,
                    ),
                )
            )
            validate_source_anchors(anchors, prepared)
        except (SourceMapError, ValueError) as exc:
            rejected.append(
                {
                    "text": str(claim.get("content") or "")[:160],
                    "reason": str(exc),
                }
            )
            continue
        value = dict(claim)
        value["source_anchors"] = [
            anchor.model_dump(mode="json") for anchor in anchors.root
        ]
        value["original_excerpt"] = "...".join(anchor.quote for anchor in anchors.root)
        anchored.append(value)
    return anchored, rejected


def _claim_with_anchors(emitted: Mapping, source: Mapping) -> dict:
    anchors = source.get("source_anchors")
    if not anchors:
        raise AnchorAlignmentError("digest/2 claim has no source anchors")
    out: dict = {}
    for key, value in emitted.items():
        if key == "location":
            continue
        out[key] = value
        if key == "provenance_chain":
            out["source_anchors"] = anchors
    if "source_anchors" not in out:
        raise AnchorAlignmentError("digest/2 claim has no emitted provenance chain")
    return out


def digest2_yaml(
    digest1_yaml: str,
    claims: Sequence[Mapping],
    prepared: PreparedPageRecord,
    snapshot: DigestRecordSnapshot,
) -> str:
    """Upgrade shared digest serialisation to the strict digest/2 envelope."""
    document = yaml.safe_load(digest1_yaml)
    if not isinstance(document, dict):
        raise AnchorAlignmentError("shared digest serialiser returned no document")

    by_category = {
        "domain_claims": [
            claim for claim in claims if claim.get("category") != "infrastructure"
        ],
        "infrastructure_claims": [
            claim for claim in claims if claim.get("category") == "infrastructure"
        ],
    }
    for key, source_claims in by_category.items():
        emitted_claims = document.get(key) or []
        if len(emitted_claims) != len(source_claims):
            raise AnchorAlignmentError("claim ordering changed during serialisation")
        if emitted_claims:
            document[key] = [
                _claim_with_anchors(emitted, source)
                for emitted, source in zip(emitted_claims, source_claims)
            ]

    snapshot_hash = digest_record_snapshot_identity(snapshot)
    document["schema"] = "anomalica/digest/2"
    document["record_snapshot_sha256"] = snapshot_hash
    validate_digest_record_projection(document.get("record"), snapshot)

    ordered = _order_document(document)

    validate_digest2_bindings(
        ordered,
        prepared,
        snapshot.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    return _yaml_dump(ordered)


def _order_document(document: Mapping) -> dict:
    order = (
        "schema",
        "extracted_at",
        "model",
        "extraction_generation",
        "extraction_config",
        "ai_usage",
        "prompts",
        "schema_enforcement",
        "review_state",
        "pre_digest",
        "record_snapshot_sha256",
        "curation",
        "record",
        "terminology",
        "nodes",
        "domain_claims",
        "infrastructure_claims",
    )
    ordered = {key: document[key] for key in order if key in document}
    ordered.update(
        {key: value for key, value in document.items() if key not in ordered}
    )
    return ordered


def record_snapshot_yaml(digest_yaml: str, snapshot: DigestRecordSnapshot) -> str:
    """Bind new record/3 digest/1 output to its mutable Record projection."""
    document = yaml.safe_load(digest_yaml)
    if not isinstance(document, dict):
        raise AnchorAlignmentError("shared digest serialiser returned no document")
    validate_digest_record_projection(document.get("record"), snapshot)
    document["record_snapshot_sha256"] = digest_record_snapshot_identity(snapshot)
    return _yaml_dump(_order_document(document))


__all__ = [
    "AnchorAlignmentError",
    "align_claim_source_anchors",
    "anchor_claims",
    "digest2_yaml",
    "digest_record_extra",
    "digest_record_snapshot",
    "is_digest2_record",
    "record3_structure",
    "record_snapshot_yaml",
    "validate_digest_record_projection",
]
