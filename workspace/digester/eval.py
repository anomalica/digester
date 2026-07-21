"""Deterministic eval: grade a digest against a record's in-body highlight gold.

The measurement gate for prompt tuning (ADR 0042). A reviewer highlights spans in
the workbench that are worth keeping; those `{{highlight-start:id}}..{{highlight-end:id}}`
markers live in the record body. This grades a produced digest against them, with
no model in the loop - the same inputs always score the same, so a prompt change's
effect is a difference of two numbers, not a matter of opinion.

COORDINATE SPACE. A claim's `quote` is verbatim from the *pre-digest* (the
materialised text the model actually read - highlights, timestamps, irrelevant
regions and image annotations all stripped; ADR 0042). So both the claim quotes
AND the highlight spans are located in the materialised pre-digest: the highlight
markers are stripped by materialise() but the prose they wrapped survives, so a
highlight's inner text is a locatable substring of the same space the claims came
from. One space, one locator, quote-driven - the model's own `location` notation
is ignored (it is unreliable and carrier-specific; realign already taught us that).

WHAT IS GOLD-BACKED AND WHAT IS NOT - the honesty line the tuning programme turns on:
  - RECALL (did highlighted spans survive into the digest) is gold-backed: a
    reviewer asserted these spans matter, so a miss is a real miss.
  - QUOTE FIDELITY (does each claim's quote appear verbatim in the source) is
    gold-backed against the source itself: a quote that does not locate is
    fabricated or paraphrased, independent of any highlight.
  - OFF-TARGET RATE (claims falling outside every highlight) is INTERPRETIVE, not
    gold-backed noise. A highlight marks a span as significant; it does NOT assert
    that everything unhighlighted is worthless (a reviewer highlights the notable,
    not exhaustively every keepable line). Treating off-target as noise is only
    valid where the gold is a complete keep-list - which a highlight set does not
    claim to be (79 gold spans against 388 claims on jon-stewart is a sampling,
    not a 79-item whitelist). So off-target is reported for inspection and as a
    RELATIVE signal between variants (fewer off-target at equal recall is plausibly
    less noisy), never as an absolute precision score. See ADR 0042.

CHAINS. Context-linked highlights (`{{highlight-context: [dependent, earlier..]}}`)
are one gold unit - the later span depends on the earlier for its referent, and a
faithful extraction resolves the coreference (names the person a later span only
calls "he"). v1 reports chain-unit membership and per-span recall; the chain-level
attribution check is a declared next increment, deliberately absent rather than
faked (a fake attribution number would corrupt exactly the decision it informs).
"""

from __future__ import annotations

import re

from anomalica_common.pre_digest import (
    materialise,
    strip_overlay_markers,
    strip_word_timestamps,
)

# Fraction of a highlight's characters that claim spans must cover for it to count
# as recalled. A highlight is a span of prose; a claim quoting half of it has found
# it. 0.5 mirrors the workbench grader (put_and_grade) so the two never disagree.
RECALL_THRESH = 0.5

_TS = re.compile(r"^\d\d:\d\d:\d\d\.\d+\s?")
_COMMENT = re.compile(r"^<!--.*-->\s*$")

_HL_START = re.compile(r"\{\{highlight-start:\s*([A-Za-z0-9]+)\s*\}\}")
_HL_END = re.compile(r"\{\{highlight-end:\s*([A-Za-z0-9]+)\s*\}\}")
_HL_CONTEXT = re.compile(r"\{\{highlight-context:\s*\[([^\]]*)\]\s*\}\}")
_HL_ANY = re.compile(r"\{\{highlight-(?:start|end|context):[^}]*\}\}")


def searchable(text: str) -> tuple[str, list[int]]:
    """A lowercased, whitespace-collapsed content string plus a per-character map
    back to `text` code-point offsets. Drops line-timestamp prefixes, speaker
    comments and markdown headers so a quote matches regardless of that framing
    (the pre-digest still carries transcript timestamps and speaker comments)."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for m in re.finditer(r".*?(?:\n|$)", text):
        line = m.group(0)
        base = m.start()
        if not line:
            continue
        if _COMMENT.match(line) or line.lstrip().startswith("#"):
            continue
        off = 0
        tm = _TS.match(line)
        if tm:
            off = tm.end()
        for j in range(off, len(line)):
            c = line[j]
            if c.isspace():
                if prev_space:
                    continue
                out.append(" ")
                idx.append(base + j)
                prev_space = True
            else:
                out.append(c.lower())
                idx.append(base + j)
                prev_space = False
    return "".join(out), idx


def _norm(quote: str) -> str:
    return re.sub(r"\s+", " ", quote.strip().lower())


def _find(qn: str, search: str, start: int = 0) -> tuple[int, str]:
    """search-string index of normalised `qn` at/after `start`, or -1. Retries
    without trailing punctuation. Returns (position, the-matched-form) so a caller
    can compute the span length and continue the search past the match."""
    if not qn:
        return -1, qn
    pos = search.find(qn, start)
    if pos < 0:
        q2 = qn.rstrip(".,;:!?\"' ")
        if not q2:
            return -1, qn
        pos = search.find(q2, start)
        if pos < 0:
            return -1, qn
        qn = q2
    return pos, qn


def locate(quote: str, search: str, idx: list[int]) -> list[int] | None:
    """Raw [start, end) code-point span of `quote` in the indexed text, or None.

    Normalises whitespace and case (neither is a fidelity concern) and retries
    without trailing punctuation. None means the quote does not appear - a
    fabricated or paraphrased quote, or a highlight whose prose was stripped."""
    pos, q = _find(_norm(quote), search)
    if pos < 0:
        return None
    return [idx[pos], idx[pos + len(q) - 1] + 1]


def _overlap(span: list[int], spans: list[tuple[int, int]]) -> int:
    """Characters of `span` covered by the union of `spans`."""
    lo, hi = span
    cov = 0
    for s, e in sorted(spans):
        s, e = max(s, lo), min(e, hi)
        if s < e:
            cov += e - s
    return cov


def parse_highlights(body: str) -> list[dict]:
    """Every highlight in the body as ``{id, text}`` (source order of the open).

    Ids match starts to ends so overlapping and nested highlights are told apart
    (record-format spec). Orphan handling per spec: a start with no matching end
    auto-closes at end of body; an end with no live open is dropped.
    """
    events = []
    for m in _HL_START.finditer(body):
        events.append((m.start(), m.end(), "start", m.group(1)))
    for m in _HL_END.finditer(body):
        events.append((m.start(), m.end(), "end", m.group(1)))
    events.sort(key=lambda e: e[0])

    open_at: dict[str, int] = {}  # id -> content-start offset (after the marker)
    order: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    for _mstart, mend, kind, hid in events:
        if kind == "start":
            if hid not in open_at:
                open_at[hid] = mend
                order.append(hid)
        else:
            start = open_at.pop(hid, None)
            if start is not None:
                # content ends at the marker's start (the end marker begins at _mstart)
                spans[hid] = (start, _mstart)
    for hid, start in open_at.items():  # unmatched start -> auto-close at body end
        spans[hid] = (start, len(body))

    result = []
    for hid in order:
        s, e = spans[hid]
        raw = body[s:e]
        # The wrapped prose is source, but it may itself carry nested markers /
        # word timestamps; strip those so the inner text matches the pre-digest.
        text = strip_word_timestamps(strip_overlay_markers(raw)).strip()
        result.append({"id": hid, "text": text})
    return result


def parse_context_chains(body: str) -> list[list[str]]:
    """Context-link groups as id lists ``[dependent, earlier, ..]`` (spec order)."""
    chains = []
    for m in _HL_CONTEXT.finditer(body):
        ids = [t.strip() for t in m.group(1).split(",") if t.strip()]
        if len(ids) >= 2:
            chains.append(ids)
    return chains


def _chain_units(highlight_ids: list[str], chains: list[list[str]]) -> list[list[str]]:
    """Union-find the highlight ids into gold units: any ids joined by a context
    edge share a unit; an unlinked highlight is a unit of one."""
    parent = {hid: hid for hid in highlight_ids}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for chain in chains:
        for other in chain[1:]:
            union(chain[0], other)
    groups: dict[str, list[str]] = {}
    for hid in highlight_ids:
        groups.setdefault(find(hid), []).append(hid)
    return list(groups.values())


def claims_of(digest: dict) -> list[dict]:
    """The digest's claims, across both formats: the two-pass single ``claims``
    list, and the legacy ``domain_claims`` + ``infrastructure_claims`` split."""
    if isinstance(digest.get("claims"), list):
        return digest["claims"]
    out: list[dict] = []
    for key in ("domain_claims", "infrastructure_claims"):
        v = digest.get(key)
        if isinstance(v, list):
            out.extend(v)
    return out


def grade_digest(
    record_body: str,
    digest: dict,
    recall_thresh: float = RECALL_THRESH,
    gold_texts: list[str] | None = None,
) -> dict:
    """Grade one digest against highlight gold.

    Gold is the record's in-body {{highlight}} spans by default; pass
    ``gold_texts`` (a list of span strings) to grade against an external gold set
    instead - used for PROVISIONAL model-drafted gold that must not be written
    into the ingest body (ADR 0042 honesty rule: such gold screens, it does not
    decide). External gold carries no context chains, so each span is its own unit.

    Returns recall (gold-backed), quote_fidelity (gold-backed), off_target_rate
    (interpretive), raw counts, and diagnostics (missed gold, off-target and
    broken-quote samples for inspection).
    """
    pre_digest = materialise(record_body)
    search, idx = searchable(pre_digest)

    # Gold: locate each span's prose in the pre-digest space. Normalise through
    # the SAME transform the index uses (drops any speaker comments / headers /
    # line-timestamps the span crosses), so a span over a multi-speaker
    # back-and-forth still matches.
    if gold_texts is not None:
        sources = [{"id": str(i), "text": t} for i, t in enumerate(gold_texts)]
        chains: list[list[str]] = []
    else:
        sources = parse_highlights(record_body)
        chains = parse_context_chains(record_body)
    gold: list[dict] = []
    unlocatable_gold = 0
    for h in sources:
        cleaned = searchable(h["text"])[0]
        span = locate(cleaned, search, idx) if cleaned else None
        if span is None:
            unlocatable_gold += 1
            continue
        gold.append(
            {"id": h["id"], "span": span, "text": pre_digest[span[0] : span[1]]}
        )
    gold_spans = [(g["span"][0], g["span"][1]) for g in gold]

    units = _chain_units([g["id"] for g in gold], chains)

    # Claims: locate each quote in the same space. MECHANICAL fidelity (this is
    # not the semantic axis - eliding away a negation that inverts sense is the
    # human grader's call, not mechanical) distinguishes four cases, because an
    # elided quote is not a fabricated one and a REORDERED one is quote-mining:
    #   contiguous - the whole quote is one verbatim span (strictest);
    #   elided     - real fragments joined with "...", located IN SOURCE ORDER;
    #                faithful, just not contiguous;
    #   reordered  - every fragment is verbatim but they are stitched OUT of source
    #                order - recomposing what the speaker said (anomalica's
    #                order-preservation rule); a fidelity FAILURE;
    #   broken     - at least one fragment does not appear at all (fabricated).
    # Order is enforced by searching each fragment only AT OR AFTER the previous
    # fragment's match; a fragment that exists only earlier is reordered, one that
    # exists nowhere is broken. A faithful claim contributes ALL fragment spans to
    # recall/off-target.
    claims = claims_of(digest)
    located: list[dict] = []  # claims whose quote is faithful (contiguous or elided)
    broken_quotes: list[dict] = []  # a fragment does not appear in the source
    reordered_quotes: list[dict] = []  # verbatim fragments, but out of source order
    n_contiguous = 0
    n_elided = 0
    for c in claims:
        quote = (c.get("quote") or "").strip()
        text = c.get("text") or ""
        whole = locate(quote, search, idx) if quote else None
        if whole is not None:
            n_contiguous += 1
            located.append({"spans": [tuple(whole)], "text": text, "quote": quote})
            continue
        fragments = [f.strip() for f in re.split(r"\.\.\.|…", quote) if f.strip()]
        if len(fragments) <= 1:
            broken_quotes.append({"quote": quote[:140], "text": text[:140]})
            continue
        spans, cursor, status = [], 0, "ordered"
        for frag in fragments:
            qn = _norm(frag)
            pos, matched = _find(qn, search, cursor)
            if pos < 0:
                # Not found forward: is it anywhere at all (reordered) or nowhere (broken)?
                status = "reordered" if _find(qn, search, 0)[0] >= 0 else "broken"
                break
            spans.append((idx[pos], idx[pos + len(matched) - 1] + 1))
            cursor = pos + len(matched)
        if status == "ordered":
            n_elided += 1
            located.append({"spans": spans, "text": text, "quote": quote})
        elif status == "reordered":
            reordered_quotes.append({"quote": quote[:140], "text": text[:140]})
        else:
            broken_quotes.append({"quote": quote[:140], "text": text[:140]})
    claim_spans = [sp for c in located for sp in c["spans"]]

    # Recall: fraction of gold spans covered past the threshold.
    covered = 0
    missed = []
    for g in gold:
        s, e = g["span"]
        frac = _overlap([s, e], claim_spans) / max(1, e - s)
        if frac >= recall_thresh:
            covered += 1
        else:
            missed.append({"id": g["id"], "text": g["text"][:140]})
    recall = covered / len(gold) if gold else None

    # Off-target (interpretive): a located claim none of whose fragment spans
    # touch any gold span.
    off_target = []
    for c in located:
        if all(_overlap([s, e], gold_spans) == 0 for s, e in c["spans"]):
            off_target.append({"quote": c["quote"][:140], "text": c["text"][:140]})
    off_target_rate = (len(off_target) / len(located)) if located else None

    # MECHANICAL fidelity: contiguous + in-order elided. Reordered and broken are
    # both failures. This is not the semantic axis (a fragment join that inverts
    # sense is the human grader's call) - report it as "mechanical".
    fidelity = (len(located) / len(claims)) if claims else None

    return {
        "claims": len(claims),
        "gold_spans": len(gold),
        "unlocatable_gold": unlocatable_gold,
        "chain_units": len(units),
        "recall": recall,
        "quote_fidelity": fidelity,
        "fidelity_contiguous": (n_contiguous / len(claims)) if claims else None,
        "contiguous": n_contiguous,
        "elided": n_elided,
        "reordered": len(reordered_quotes),
        "broken": len(broken_quotes),
        "off_target_rate": off_target_rate,
        "off_target_count": len(off_target),
        "covered": covered,
        "missed": missed,
        "off_target": off_target,
        "reordered_quotes": reordered_quotes,
        "broken_quotes": broken_quotes,
    }
