"""The account layer: which telling does each claim belong to?

Extraction pulls atomic facts and loses the shape of the source. An interview
in which somebody describes three abductions yields a pile of correct claims
about craft and places, with nothing recording that they belong to three
separate stories. This adds the missing level.

AN ACCOUNT IS A SPAN OF A SOURCE, NOT AN ENTITY. That is the whole design, and
it is the lesson of `matter`, the node type that was removed for folding four
ways and left 134 legacy nodes behind: a vague type absorbs whatever nobody
else wanted. An account cannot do that, because it is not a thing in the world
at all - it is a stretch of THIS record in which one story is told. Two sources
telling the same story produce two accounts, correctly, because there were two
tellings. The layer above - a theme across independent cases - already exists
as `Pattern` in node-types.md, is curator-created and holds a
three-independent-cases bar; this pass must never reach for it.

THE BINDING IS ARITHMETIC, NOT A SECOND OPINION. 38,858 of 38,863 claims in the
live graph already carry a located span, so which account a claim sits in is a
comparison of intervals and costs nothing. The model is asked only for the
thing it can see and arithmetic cannot: where one story stops and the next
begins. Nothing here asks a model to re-decide what a claim says.

THE FLOOR IS APPLIED AFTER BINDING, for the same reason. "Enough substance to
be a story" is checkable once the claims are attached, so a candidate that
attracted almost nothing is dropped by counting rather than by asking the model
to judge its own output.
"""

from __future__ import annotations

import re
from typing import Any

# A candidate needs this many located claims inside it to survive. Below it the
# passage was a digression, an aside or a remark - the model is asked to apply
# the same floor when emitting, and this catches what it lets through.
MIN_CLAIMS = 3

VALID_TELLER_ROLES = (
    "experienced",
    "investigated",
    "was_told",
    "briefed_or_read",
    "unclear",
)

_TIMECODE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d))?")
_CHAR_SPAN = re.compile(r"char:(\d+)-(\d+)")
_CH_SPAN = re.compile(r"ch\d+:(\d+)-(\d+)")


def parse_seconds(value: str) -> float | None:
    """Seconds from a timecode, or None if this is not one."""
    m = _TIMECODE.fullmatch((value or "").strip())
    if not m:
        return None
    h, mi, s, d = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + (int(d) / 10 if d else 0.0)


def claim_position(location: str | None) -> float | None:
    """Where a claim sits, as a single comparable number.

    Timecoded records give seconds; char and chapter spans give the start
    offset. A claim with a prose location ("page 3, paragraph 2") has no
    position and cannot be bound - it is counted as unbindable rather than
    guessed at, because a claim assigned to the wrong story is worse than one
    assigned to none.
    """
    if not location:
        return None
    m = _TIMECODE.search(location)
    if m:
        return parse_seconds(m.group(0))
    for pattern in (_CHAR_SPAN, _CH_SPAN):
        m = pattern.search(location)
        if m:
            return float(m.group(1))
    return None


def locate_phrase(body: str, phrase: str) -> int | None:
    """Character offset of a verbatim phrase, tolerant of whitespace.

    The model is asked for opening and closing phrases on records with no
    timecodes. The pre-digest breaks lines where the quote has spaces, so an
    exact search misses - the same trap the quote locator hit, costing 13.6% of
    the corpus, so it is handled here from the start rather than after.
    """
    phrase = (phrase or "").strip()
    if not phrase or not body:
        return None
    i = body.find(phrase)
    if i >= 0:
        return i
    tokens = phrase.split()[:12]
    if not tokens:
        return None
    m = re.search(r"\s+".join(re.escape(t) for t in tokens), body)
    return m.start() if m else None


def resolve_span(account: dict, body: str) -> tuple[float, float] | None:
    """An account's (start, end) as comparable numbers, or None if unresolvable."""
    start_raw = account.get("span_start") or ""
    end_raw = account.get("span_end") or ""
    start, end = parse_seconds(start_raw), parse_seconds(end_raw)
    if start is None or end is None:
        start = locate_phrase(body, start_raw)
        end_at = locate_phrase(body, end_raw)
        # The closing phrase marks where the passage ENDS, so the span runs to
        # the end of that phrase rather than its start.
        end = None if end_at is None else end_at + len(end_raw.strip())
    if start is None or end is None or end <= start:
        return None
    return float(start), float(end)


def bind(accounts: list[dict], claims: list[dict], body: str = "") -> dict:
    """Attach each claim to the account whose span contains it.

    Returns counts and mutates nothing: each claim gains `account_id` only via
    the returned mapping, so a caller decides whether to write it. Overlapping
    spans go to the SMALLEST containing account, because a nested telling is
    more specific than the passage it sits inside.
    """
    resolved = []
    for i, a in enumerate(accounts):
        spans = []
        primary = resolve_span(a, body)
        if primary:
            spans.append(primary)
        for extra in a.get("also_spans") or []:
            span = resolve_span(extra, body)
            if span:
                spans.append(span)
        resolved.append(spans)

    counts = {"bound": 0, "unbindable": 0, "outside": 0}
    mapping: dict[str, str] = {}
    per_account: dict[int, int] = {i: 0 for i in range(len(accounts))}
    for c in claims:
        pos = claim_position(c.get("location"))
        if pos is None:
            counts["unbindable"] += 1
            continue
        best, best_width = None, None
        for i, spans in enumerate(resolved):
            for start, end in spans:
                if start <= pos <= end:
                    width = end - start
                    if best_width is None or width < best_width:
                        best, best_width = i, width
        if best is None:
            counts["outside"] += 1
            continue
        counts["bound"] += 1
        per_account[best] += 1
        if c.get("id"):
            mapping[c["id"]] = account_id(accounts[best], best)
    return {"counts": counts, "claim_to_account": mapping, "per_account": per_account}


def account_id(account: dict, index: int) -> str:
    """A stable id from the account's own position, not its title.

    Position, because a title is model-written prose that changes between runs
    while the passage does not - keying on the title would make two runs of the
    same record disagree about which account is which.
    """
    start = (account.get("span_start") or "").strip() or f"i{index}"
    return "acct-" + re.sub(r"[^A-Za-z0-9.]+", "-", start).strip("-").lower()


def apply_floor(
    accounts: list[dict], per_account: dict[int, int], min_claims: int = MIN_CLAIMS
) -> tuple[list[dict], list[dict]]:
    """Split candidates into those that clear the floor and those that do not.

    Kept separate rather than deleted: a dropped candidate is evidence about
    the pass's precision, and throwing it away would leave nothing to measure
    the floor against.
    """
    kept, dropped = [], []
    for i, a in enumerate(accounts):
        has_subject = bool((a.get("subject") or "").strip())
        has_anchor = bool(
            (a.get("when") or "").strip() or (a.get("where") or "").strip()
        )
        enough = per_account.get(i, 0) >= min_claims
        if has_subject and has_anchor and enough:
            kept.append(a)
        else:
            dropped.append(
                {
                    **a,
                    "dropped_because": (
                        "no subject"
                        if not has_subject
                        else "no when or where"
                        if not has_anchor
                        else f"only {per_account.get(i, 0)} claims"
                    ),
                }
            )
    return kept, dropped


def conform(account: dict, index: int) -> dict[str, Any]:
    """One account in the shape the digest stores, with its id."""
    role = account.get("teller_role")
    return {
        "id": account_id(account, index),
        "title": (account.get("title") or "").strip(),
        "subject": (account.get("subject") or "").strip(),
        **(
            {"when": account["when"].strip()}
            if (account.get("when") or "").strip()
            else {}
        ),
        **(
            {"where": account["where"].strip()}
            if (account.get("where") or "").strip()
            else {}
        ),
        "span": f"{account.get('span_start', '')}-{account.get('span_end', '')}",
        "teller_role": role if role in VALID_TELLER_ROLES else "unclear",
    }
