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
    reviewer asserted these spans matter, so a miss is a real miss. It is
    COVERAGE-WEIGHTED, never binary per-highlight - a highlight carrying several
    facts scores the fraction of its characters that claims cover (1 of 3 facts
    reads 33%, not a hit). highlight != claim granularity is by design; atomicity
    is the extraction's obligation, measured here as coverage.
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

GOLD UNITS AND CONTEXT (anomalica's ruling on Mark's real chains). EACH highlight
is its own gold unit = itself + its transitive ANCESTOR closure via
`{{highlight-context: [dependent, earlier..]}}` edges. This is NOT
union-by-component: a shared person-intro hub is context to many units WITHOUT
merging them (union would collapse a 39-node web into one unit - wrong). The
closure is CONTEXT for the coreference requirement - a faithful extraction of a
dependent span resolves the referent (names the person a later span only calls
"he") using its closure. Dangling context refs (an ancestor since deleted) are
dropped as absent context, never a failure of the citing unit. The loader reports
the unit structure (units, units-with-context, max closure depth).

Per-unit coreference is scored as a MECHANICAL proxy ("coref-mech"): a recalled
dependent unit (bare pronoun, no own name, non-empty closure) PASSES if a covering
claim NAMES a referent rather than echoing the pronoun. Named-a-referent is
mechanical; named-the-RIGHT-referent is the human axis - so it is reported as
passed/applicable (never a bare rate, never composited) and each pass emits the
name seen plus the candidate closure hubs, making the semantic check cheap to
spot-check by sampling instead of pretending it is measured.
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


# Coreference proxy (MECHANICAL, confirmed by anomalica/master). A dependent span
# ("he said...") whose referent lives in an ancestor highlight is coreference-
# applicable; it PASSES if a covering claim NAMES a referent rather than echoing
# the bare pronoun. "Named a referent" is mechanical; "named the RIGHT referent" is
# the human axis - so the grader emits per-passing-unit which name it saw for cheap
# human spot-checking, and never blends this into a composite score.
_THIRD_PERSON = re.compile(
    r"\b(he|she|they|him|her|them|his|their|hers|theirs)\b", re.I
)
# Titlecase tokens that are ordinary sentence-openers, not names - so a span that
# merely STARTS with one is not treated as naming its own referent.
_CAP_STOPWORDS = frozenset(
    {
        "The",
        "A",
        "An",
        "This",
        "That",
        "These",
        "Those",
        "It",
        "He",
        "She",
        "They",
        "There",
        "Here",
        "When",
        "Then",
        "But",
        "And",
        "So",
        "If",
        "As",
        "At",
        "In",
        "On",
        "Of",
        "To",
        "We",
        "You",
        "I",
        "His",
        "Her",
        "Their",
        "What",
        "Who",
        "Why",
        "How",
        "Because",
        "Since",
        "After",
        "Before",
    }
)
_PROPER = re.compile(r"\b([A-Z][A-Za-z]+)\b")


def _named_referents(text: str) -> list[str]:
    """Proper-noun tokens that plausibly name someone/something - a mechanical
    stand-in for 'the claim named a referent'. Sentence-opener capitals are
    excluded so a bare 'He ...' does not count as naming."""
    return [t for t in _PROPER.findall(text) if t not in _CAP_STOPWORDS]


def _is_dependent(span_text: str) -> bool:
    """DEPRECATED heuristic, retained only for callers that still import it.

    Superseded because it second-guessed the human gold. A reviewer who draws a
    context edge has ALREADY DECLARED that span dependent - that is what the edge
    means - so inferring dependency from prose is both unnecessary and wrong. On
    Mark's gold this rule selected 10 of the 104 context-bearing units: the
    "names no proper noun" clause alone cut 82 to 10, because a span like "Bob
    Lazar said it was the company that hired him" names Lazar while depending on
    its ancestors for "it" and "him". Presence of A name does not make a span
    self-contained.
    """
    return bool(_THIRD_PERSON.search(span_text)) and not _named_referents(span_text)


def _closure_referents(
    unit_id: str, closures: dict[str, set[str]], gold_by_id: dict[str, dict]
) -> set[str]:
    """Proper nouns named in a unit's ANCESTOR spans - the referents a dependent
    span is expected to resolve to.

    This is what makes the coreference check a real test rather than a proxy: it
    asks whether the extraction carried the referent FORWARD FROM THE LINKED
    ANCESTOR, not merely whether it happened to name somebody.
    """
    out: set[str] = set()
    for anc in closures.get(unit_id, ()):
        g = gold_by_id.get(anc)
        if g:
            out.update(_named_referents(g["text"]))
    return out


def _overlap(span: list[int], spans: list[tuple[int, int]]) -> int:
    """Characters of `span` covered by the UNION of `spans`.

    The union, and it has to be: summing each span's overlap counted a
    character once per claim that covered it, so two claims quoting the same
    sentence scored it twice and coverage could exceed the span's own length.
    Recall is a mean of those fractions, so it read above 1.0 and rewarded a
    model in proportion to how much it repeated itself - which is the opposite
    of what it is for, and it silently favoured the more verbose model in every
    comparison drawn from it.
    """
    lo, hi = span
    cov = 0
    start = end = None
    for s, e in sorted(spans):
        s, e = max(s, lo), min(e, hi)
        if s >= e:
            continue
        if end is None or s > end:
            if end is not None:
                cov += end - start
            start, end = s, e
        else:
            end = max(end, e)
    if end is not None:
        cov += end - start
    return cov


def parse_highlights(body: str) -> list[dict]:
    """Every highlight in the body as ``{id, text}`` (source order of the open).

    Ids match starts to ends so overlapping and nested highlights are told apart
    (ingest-format spec). Orphan handling per spec: a start with no matching end
    auto-closes at end of body; an end with no live open is dropped.
    """
    events = []
    for m in _HL_START.finditer(body):
        events.append((m.start(), m.end(), "start", m.group(1)))
    for m in _HL_END.finditer(body):
        events.append((m.start(), m.end(), "end", m.group(1)))
    events.sort(key=lambda e: e[0])

    # A highlight may be EXTENDED: one id, several start/end pairs, so its
    # evidence can sit at the top and bottom of a paragraph with the digression
    # between them left out. One highlight, one id, ONE expected claim.
    #
    # So ranges are a LIST per id, not a single tuple. As a tuple the second pair
    # overwrote the first - and worse than the obvious failure: `order` gained the
    # id a second time while `spans[hid]` held only the last range, so the output
    # was two copies of the SECOND part with the first silently gone. Nothing
    # errored, and the gold was quietly wrong.
    open_at: dict[str, int] = {}  # id -> content-start offset (after the marker)
    order: list[str] = []
    spans: dict[str, list[tuple[int, int]]] = {}
    for _mstart, mend, kind, hid in events:
        if kind == "start":
            if hid not in open_at:
                open_at[hid] = mend
                if hid not in order:
                    order.append(hid)
        else:
            start = open_at.pop(hid, None)
            if start is not None:
                # content ends at the marker's start (the end marker begins at _mstart)
                spans.setdefault(hid, []).append((start, _mstart))
    for hid, start in open_at.items():  # unmatched start -> auto-close at body end
        spans.setdefault(hid, []).append((start, len(body)))

    result = []
    for hid in order:
        # Parts JOINED IN BODY ORDER with an elision marker, never concatenated.
        # Running one part's opening into another's ending manufactures a sentence
        # the source never uttered - the false-quotation failure arriving through
        # the grader rather than through an extraction.
        parts = sorted(spans.get(hid, []))
        raw = " [...] ".join(body[s:e] for s, e in parts)
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


def ancestor_closures(
    highlight_ids: list[str], chains: list[list[str]]
) -> dict[str, set[str]]:
    """Each highlight's transitive ANCESTOR closure (its context), per anomalica's
    gold-unit ruling. A context edge ``[dependent, earlier..]`` means the dependent
    span needs the earlier ones for its referent; the closure follows those edges
    backwards. Crucially this is NOT union-by-component: EACH highlight is its own
    gold unit = itself + its closure, and a shared ancestor (a person-intro hub)
    appears in many units WITHOUT merging them. Dangling refs (an ancestor id that
    is not a present highlight - a since-deleted span) are dropped as absent
    context, never a failure of the citing unit. Cycles are broken by a visited set.
    """
    parents: dict[str, set[str]] = {}
    for chain in chains:
        parents.setdefault(chain[0], set()).update(chain[1:])
    present = set(highlight_ids)
    closures: dict[str, set[str]] = {}
    for hid in highlight_ids:
        seen: set[str] = set()
        stack = list(parents.get(hid, ()))
        while stack:
            a = stack.pop()
            if a in seen:
                continue
            seen.add(a)
            stack.extend(parents.get(a, ()))
        closures[hid] = {a for a in seen if a in present}  # drop dangling refs
    return closures


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

    # Gold-unit structure (anomalica's ruling): EACH highlight is its own unit =
    # itself + its ancestor closure (context). A shared ancestor is context to many
    # units without merging them.
    closures = ancestor_closures([g["id"] for g in gold], chains)
    units_with_context = sum(1 for g in gold if closures.get(g["id"]))
    max_context = max((len(closures.get(g["id"], ())) for g in gold), default=0)

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
        refs = [r.get("name") for r in (c.get("refs") or []) if r.get("name")]
        whole = locate(quote, search, idx) if quote else None
        if whole is not None:
            n_contiguous += 1
            located.append(
                {"spans": [tuple(whole)], "text": text, "quote": quote, "refs": refs}
            )
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
            located.append({"spans": spans, "text": text, "quote": quote, "refs": refs})
        elif status == "reordered":
            reordered_quotes.append({"quote": quote[:140], "text": text[:140]})
        else:
            broken_quotes.append({"quote": quote[:140], "text": text[:140]})
    claim_spans = [sp for c in located for sp in c["spans"]]

    # Recall is COVERAGE-WEIGHTED, never binary per-highlight (anomalica's pin): a
    # highlight carrying several facts must score PARTIALLY when claims cover only
    # some of it - a model extracting 1 of 3 facts reads 33%, not 100%. So recall
    # is the mean fraction of each gold span's characters that claim spans cover,
    # NOT a count of highlights past a threshold. highlight!=claim granularity is by
    # design; atomicity is the extraction's obligation, measured as coverage here.
    # The threshold survives only to flag near-missed units for inspection.
    coverage_sum = 0.0
    unit_coverage: list[dict] = []
    missed = []
    for g in gold:
        s, e = g["span"]
        frac = _overlap([s, e], claim_spans) / max(1, e - s)
        coverage_sum += frac
        unit_coverage.append(
            {
                "id": g["id"],
                "coverage": round(frac, 3),
                "context": sorted(closures.get(g["id"], ())),
            }
        )
        if frac < recall_thresh:
            missed.append(
                {"id": g["id"], "coverage": round(frac, 3), "text": g["text"][:120]}
            )
    recall = coverage_sum / len(gold) if gold else None
    covered = sum(1 for u in unit_coverage if u["coverage"] >= recall_thresh)

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
    # PER DISTINCT QUOTE, not per claim. Verbatimness is a property of a QUOTE;
    # how many claims cite it is a different fact. Counting per claim charges one
    # unlocatable passage once for every claim that cites it - measured on the
    # Papua New Guinea record, three claims sharing one bad quote scored three
    # broken and dropped fidelity to 83.3% off a single underlying failure. That
    # structurally penalises any extraction that decomposes a passage into more
    # claims, so the metric fell as reasoning effort rose with no change in
    # quoting behaviour required. It was measuring decomposition, not fidelity.
    def _q(c):
        return _norm(c.get("quote") or "")

    all_quotes = {_q(c) for c in claims if _q(c)}
    located_quotes = {_q(c) for c in located if _q(c)}
    fidelity = (len(located_quotes) / len(all_quotes)) if all_quotes else None

    # Coreference (MECHANICAL proxy, never composited). A recalled unit is
    # applicable if its span is dependent (bare pronoun, no own name) and it has a
    # closure; it passes if a covering claim names a referent. Emit per pass the
    # name seen and the closure hubs it presumably resolves to, so the semantic
    # axis (RIGHT referent) is cheap to spot-check by sampling.
    # APPLICABILITY IS THE REVIEWER'S DECLARATION, not our inference: a unit is
    # coreference-applicable when it has a non-empty ancestor closure, because
    # drawing that context edge IS the human saying "this span needs the earlier
    # one to be understood". The previous rule additionally demanded a pronoun and
    # NO proper noun, which selected 10 of 104 - it was overriding the gold with a
    # worse guess, and left the chains Mark hand-built almost entirely untested.
    #
    # PASSING now means resolving to the RIGHT source: a covering claim must name a
    # referent that appears in the unit's ANCESTOR spans, not merely name somebody.
    # A unit whose ancestors name nobody cannot test name-resolution at all, so it
    # is counted UNTESTABLE rather than silently passed or failed.
    gold_by_id = {g["id"]: g for g in gold}
    coref_applicable = 0
    coref_passed = 0
    coref_untestable = 0
    coref_audit: list[dict] = []
    for g in gold:
        closure = closures.get(g["id"]) or set()
        if not closure:
            continue
        gs = (g["span"][0], g["span"][1])
        covering = [c for c in located if _overlap(list(gs), c["spans"]) > 0]
        if not covering:
            continue  # not recalled at all -> a recall miss, not a coref failure
        candidates = _closure_referents(g["id"], closures, gold_by_id)
        if not candidates:
            coref_untestable += 1
            continue
        coref_applicable += 1
        named: list[str] = []
        for c in covering:
            named += _named_referents(c["text"]) + list(c.get("refs", []))
        # A ref name is "Last, First"; an ancestor may name either part.
        hit = sorted(
            {cand for cand in candidates for n in named if cand in n or n in cand}
        )
        if hit:
            coref_passed += 1
        coref_audit.append(
            {
                "unit": g["id"],
                "resolved": bool(hit),
                "matched": hit[:5],
                "expected_from_ancestors": sorted(candidates)[:8],
                "closure_hubs": sorted(closure),
            }
        )
    coref_rate = (coref_passed / coref_applicable) if coref_applicable else None

    return {
        "claims": len(claims),
        "gold_spans": len(gold),
        "gold_units": len(gold),  # each highlight is its own unit (anomalica ruling)
        "units_with_context": units_with_context,
        "max_context": max_context,
        "unlocatable_gold": unlocatable_gold,
        "recall": recall,  # coverage-weighted mean, NOT a hit/miss count
        "fully_covered": covered,  # units at >= recall_thresh coverage (diagnostic only)
        "quote_fidelity": fidelity,  # over DISTINCT quotes
        "distinct_quotes": len(all_quotes),
        "fidelity_contiguous": (n_contiguous / len(claims)) if claims else None,
        "contiguous": n_contiguous,
        "elided": n_elided,
        "reordered": len(reordered_quotes),
        "broken": len(broken_quotes),
        "off_target_rate": off_target_rate,
        "off_target_count": len(off_target),
        "coref_applicable": coref_applicable,
        "coref_passed": coref_passed,
        "coref_untestable": coref_untestable,  # closure names nobody to resolve to
        "coref_rate": coref_rate,  # mechanical: named A referent, not the RIGHT one
        "missed": missed,
        "unit_coverage": unit_coverage,
        "off_target": off_target,
        "reordered_quotes": reordered_quotes,
        "broken_quotes": broken_quotes,
        "coref_audit": coref_audit,  # per pass: name seen + candidate closure hubs
    }
