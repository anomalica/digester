"""Validation and deterministic review batches for compact human gold."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from digester.eval import (
    _find,
    _norm,
    claims_of,
    context_dependencies,
    parse_context_chains,
    parse_highlights,
    searchable,
)


SCHEMA = "anomalica/highlight-gold/1"
DECISIONS = {"accept", "adjust", "split", "reject", "defer"}
_ANNOTATION = re.compile(r"\{\{[^}]*\}\}")


class HighlightGoldError(ValueError):
    """The sidecar is stale, unauthenticated, or violates the contract."""


def _timestamp(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise HighlightGoldError(f"{field} must be an authenticated UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HighlightGoldError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise HighlightGoldError(f"{field} must include a timezone")


def _identity(value: object, field: str) -> None:
    if not isinstance(value, dict):
        raise HighlightGoldError(f"{field} lacks authenticated reviewer identity")
    if any(
        not isinstance(value.get(key), str) or not value[key].strip()
        for key in ("issuer", "subject", "name")
    ):
        raise HighlightGoldError(f"{field} lacks authenticated reviewer identity")


def _inside(parts: list[dict], start: int, end: int) -> bool:
    return bool(parts) and all(
        start <= part["start"] and part["end"] <= end for part in parts
    )


def _crosses(parts: list[dict], start: int, end: int) -> bool:
    return any(
        part["start"] < end and start < part["end"] for part in parts
    ) and not _inside(parts, start, end)


def validate(record_hash: str, body: str, document: dict) -> dict:
    """Validate an authenticated sidecar and return its derived review state."""
    if document.get("schema") != SCHEMA:
        raise HighlightGoldError("unsupported highlight-gold schema")
    if document.get("record_hash") != record_hash:
        raise HighlightGoldError("highlight gold targets another record")
    expected_body_hash = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    if document.get("body_sha256") != expected_body_hash:
        raise HighlightGoldError("highlight gold body hash does not match")

    highlights = parse_highlights(body)
    by_id = {item["id"]: item for item in highlights}
    closures, unresolved = context_dependencies(highlights, parse_context_chains(body))
    ranges = document.get("ranges")
    if not isinstance(ranges, list):
        raise HighlightGoldError("highlight gold ranges must be a list")

    seen_range_ids: set[str] = set()
    bounds: list[tuple[int, int]] = []
    accepted_facts: list[dict] = []
    derived_ranges: list[dict] = []
    for index, review_range in enumerate(ranges):
        field = f"ranges[{index}]"
        if not isinstance(review_range, dict):
            raise HighlightGoldError(f"{field} is not a mapping")
        range_id = review_range.get("id")
        start, end = review_range.get("start"), review_range.get("end")
        if not isinstance(range_id, str) or not range_id or range_id in seen_range_ids:
            raise HighlightGoldError(f"{field}.id is missing or duplicated")
        seen_range_ids.add(range_id)
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or start >= end
            or end > len(body)
        ):
            raise HighlightGoldError(f"{field} has invalid offsets")
        bounds.append((start, end))
        _identity(review_range.get("reviewer"), f"{field}.reviewer")
        _timestamp(review_range.get("updated_at"), f"{field}.updated_at")
        complete = review_range.get("complete")
        if not isinstance(complete, bool):
            raise HighlightGoldError(f"{field}.complete must be boolean")
        if complete:
            _timestamp(review_range.get("attested_at"), f"{field}.attested_at")
        elif "attested_at" in review_range:
            raise HighlightGoldError(
                f"{field}.attested_at is forbidden while incomplete"
            )

        contained = [item for item in highlights if _inside(item["parts"], start, end)]
        boundary = [
            item["id"] for item in highlights if _crosses(item["parts"], start, end)
        ]
        units = review_range.get("units")
        if not isinstance(units, list):
            raise HighlightGoldError(f"{field}.units must be a list")
        decisions: dict[str, dict] = {}
        previous_position = -1
        source_positions = {item["id"]: pos for pos, item in enumerate(highlights)}
        for unit_index, unit in enumerate(units):
            unit_field = f"{field}.units[{unit_index}]"
            if not isinstance(unit, dict):
                raise HighlightGoldError(f"{unit_field} is not a mapping")
            hid, decision = unit.get("highlight_id"), unit.get("decision")
            if (
                hid in decisions
                or hid not in by_id
                or not _inside(by_id[hid]["parts"], start, end)
            ):
                raise HighlightGoldError(
                    f"{unit_field} does not name a unique in-range highlight"
                )
            if source_positions[hid] < previous_position:
                raise HighlightGoldError(f"{field}.units are not in source order")
            previous_position = source_positions[hid]
            if decision not in DECISIONS:
                raise HighlightGoldError(f"{unit_field} has an invalid decision")
            facts = unit.get("facts")
            required = (
                1
                if decision in {"accept", "adjust"}
                else 2
                if decision == "split"
                else 0
            )
            if required:
                if (
                    not isinstance(facts, list)
                    or len(facts) < required
                    or any(
                        not isinstance(fact, str) or not fact.strip() for fact in facts
                    )
                ):
                    raise HighlightGoldError(f"{unit_field} has invalid accepted facts")
                if decision == "accept" and len(facts) != 1:
                    raise HighlightGoldError(
                        f"{unit_field} accept requires exactly one fact"
                    )
                accepted_facts.extend(
                    {"highlight_id": hid, "fact": fact, "range_id": range_id}
                    for fact in facts
                )
            elif facts is not None:
                raise HighlightGoldError(f"{unit_field} forbids facts")
            if unresolved.get(hid) and decision != "defer":
                raise HighlightGoldError(f"{unit_field} must defer unresolved context")
            decisions[hid] = unit

        required_ids = {item["id"] for item in contained}
        if complete and (
            set(decisions) != required_ids
            or any(unit["decision"] == "defer" for unit in decisions.values())
        ):
            raise HighlightGoldError(
                f"{field} cannot be complete with missing or deferred units"
            )
        if complete and any(unresolved.get(hid) for hid in required_ids):
            raise HighlightGoldError(
                f"{field} cannot be complete with unresolved context"
            )
        derived_ranges.append(
            {
                "id": range_id,
                "start": start,
                "end": end,
                "complete": complete,
                "contained_ids": [item["id"] for item in contained],
                "boundary_ids": boundary,
                "decisions": decisions,
            }
        )

    for previous, current in zip(sorted(bounds), sorted(bounds)[1:]):
        if current[0] < previous[1]:
            raise HighlightGoldError("highlight gold ranges overlap")
    return {
        "schema": SCHEMA,
        "highlights": highlights,
        "closures": closures,
        "unresolved": unresolved,
        "ranges": derived_ranges,
        "accepted_facts": accepted_facts,
        "gold_units": len({item["highlight_id"] for item in accepted_facts}),
        "gold_facts": len(accepted_facts),
    }


def _raw_search(body: str) -> tuple[str, list[int]]:
    masked = _ANNOTATION.sub(lambda match: " " * len(match.group(0)), body)
    return searchable(masked)


def _display_context(body: str, item: dict, radius: int = 240) -> str:
    start = max(0, item["parts"][0]["start"] - radius)
    end = min(len(body), item["parts"][-1]["end"] + radius)
    plain = _ANNOTATION.sub(" ", body[start:end])
    return re.sub(r"\s+", " ", plain).strip()


def _claim_parts(claim: dict, search: str, index: list[int]) -> list[tuple[int, int]]:
    quote = str(claim.get("quote") or "").strip()
    fragments = (
        [quote]
        if "..." not in quote and "…" not in quote
        else [part.strip() for part in re.split(r"\.\.\.|…", quote) if part.strip()]
    )
    spans: list[tuple[int, int]] = []
    cursor = 0
    for fragment in fragments:
        position, matched = _find(_norm(fragment), search, cursor)
        if position < 0:
            return []
        spans.append((index[position], index[position + len(matched) - 1] + 1))
        cursor = position + len(matched)
    return spans


def review_batch(
    record_hash: str,
    body: str,
    document: dict,
    digests: list[dict] | None = None,
    *,
    range_id: str | None = None,
    limit: int = 5,
) -> dict:
    """Return the next 3-5 review units and optional deduplicated proposals."""
    if not 3 <= limit <= 5:
        raise HighlightGoldError("batch limit must be between 3 and 5")
    state = validate(record_hash, body, document)
    ranges = state["ranges"]
    review_range = (
        next((item for item in ranges if item["id"] == range_id), None)
        if range_id
        else next((item for item in ranges if not item["complete"]), None)
    )
    if review_range is None:
        return {
            "schema": "anomalica/highlight-gold-batch/1",
            "range_id": range_id,
            "units": [],
        }

    by_id = {item["id"]: item for item in state["highlights"]}
    decisions = review_range["decisions"]
    fresh = [hid for hid in review_range["contained_ids"] if hid not in decisions]
    deferred = [
        hid
        for hid in review_range["contained_ids"]
        if decisions.get(hid, {}).get("decision") == "defer"
    ]
    selected = (fresh + deferred)[:limit]
    search, index = _raw_search(body)
    proposals: dict[str, dict[str, Any]] = {}
    for digest in digests or []:
        model = str(digest.get("model") or "unknown")
        for claim in claims_of(digest):
            text = str(claim.get("text") or claim.get("claim") or "").strip()
            key = _norm(text)
            spans = _claim_parts(claim, search, index)
            if not key or not spans:
                continue
            proposal = proposals.setdefault(
                key,
                {
                    "fact": text,
                    "quote": claim.get("quote") or "",
                    "models": [],
                    "spans": spans,
                },
            )
            if model not in proposal["models"]:
                proposal["models"].append(model)

    units = []
    for hid in selected:
        item = by_id[hid]
        linked = [
            by_id[target]
            for target in sorted(
                state["closures"].get(hid, ()),
                key=lambda target: by_id[target]["parts"][0]["start"],
            )
        ]
        own_ranges = [(part["start"], part["end"]) for part in item["parts"]]
        unit_proposals = []
        for proposal in proposals.values():
            if any(
                max(start, pstart) < min(end, pend)
                for start, end in own_ranges
                for pstart, pend in proposal["spans"]
            ):
                unit_proposals.append(
                    {key: value for key, value in proposal.items() if key != "spans"}
                )
        units.append(
            {
                "highlight_id": hid,
                "parts": [part["text"] for part in item["parts"]],
                "source_context": _display_context(body, item),
                "context": [
                    {
                        "highlight_id": linked_item["id"],
                        "parts": [part["text"] for part in linked_item["parts"]],
                    }
                    for linked_item in linked
                ],
                "unresolved_context": sorted(state["unresolved"].get(hid, ())),
                "proposed_facts": unit_proposals,
                "proposal_status": "optional-model-output-not-gold",
            }
        )
    return {
        "schema": "anomalica/highlight-gold-batch/1",
        "range_id": review_range["id"],
        "units": units,
    }
