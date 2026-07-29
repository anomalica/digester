"""Deterministic re-alignment of claim quotes to source word timing.

Post-process, no AI: given a claim's verbatim quote and a record's word stream
with per-word timestamps, recover the (start, end) seconds the quote spans, plus
a coverage confidence. The prototype (benchmarks/align_prototype.py) established
that aligning on the verbatim quote - never the paraphrased text - reproduces the
source timing at high coverage, model-independently.

Carrier-agnostic: `align_quote` takes the (words, times) arrays directly. The
arrays come from one of the adapters - record/1 sentence prefixes, record/2
inline {{t:}} tokens, or the flattened anomalica/words/1 sidecar - so the
inline-vs-sidecar carrier decision does not touch the alignment logic.

The duplicate-phrase fix (a short quote matching several transcript positions):
score the FULL quote in a window around each candidate start and take the best,
so the occurrence whose neighbouring words also match wins. See ADR draft
word-timestamp-realignment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_WORD = re.compile(r"[a-z0-9]+")
_TS_LINE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d)\s+(.*)$")
_WORD_TOKEN = re.compile(r"\{\{t:([0-9.]+)\}\}([^{]*)")

# A candidate is plausible only if its full-quote coverage clears this floor;
# below it we treat the quote as unalignable (heavily paraphrased / not present).
_MIN_COVERAGE = 0.5
# Two candidates whose coverage is within this band but whose start times are
# more than _AMBIG_GAP_S apart are flagged ambiguous (the duplicate-phrase case).
_AMBIG_COVERAGE_BAND = 0.05
_AMBIG_GAP_S = 5.0


@dataclass
class AlignResult:
    start: float
    end: float
    coverage: float
    resolution: str  # "word" | "sentence" | "turn"
    ambiguous: bool


def tokenise(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def words_from_record1(body: str) -> tuple[list[str], list[float]]:
    """(words, per-word seconds) from a record/1 body's sentence-level prefixes.

    Each word inherits its sentence's start time, so resolution is "sentence".
    """
    words: list[str] = []
    times: list[float] = []
    for line in body.splitlines():
        m = _TS_LINE.match(line.strip())
        if not m:
            continue
        h, mi, s, d, text = m.groups()
        sec = int(h) * 3600 + int(mi) * 60 + int(s) + int(d) / 10
        for w in tokenise(text):
            words.append(w)
            times.append(sec)
    return words, times


def words_from_record2(body: str) -> tuple[list[str], list[float]]:
    """(words, per-word seconds) from a record/2 body's inline {{t:}} tokens."""
    words: list[str] = []
    times: list[float] = []
    for m in _WORD_TOKEN.finditer(body):
        t = float(m.group(1))
        for w in tokenise(m.group(2)):
            words.append(w)
            times.append(t)
    return words, times


def words_from_sidecar(sidecar: dict) -> tuple[list[str], list[float]]:
    """(words, per-word seconds) from a flattened anomalica/words/1 sidecar.

    Flattening every segment's words in document order is 1:1 with the clean
    body's spoken-word tokens (exact by construction - the body is rendered by
    joining these tokens). Word start time is used as the per-word timestamp.
    """
    words: list[str] = []
    times: list[float] = []
    for seg in sidecar.get("segments") or []:
        for wd in seg.get("words") or []:
            for w in tokenise(wd.get("w", "")):
                words.append(w)
                times.append(float(wd.get("start", seg.get("start", 0.0))))
    return words, times


def _candidate_starts(qt: list[str], src: list[str], seed_len: int = 4) -> list[int]:
    """Source positions where the quote's leading tokens match (allowing one
    mismatch), used as anchors for windowed full-quote scoring."""
    seed = qt[: min(seed_len, len(qt))]
    if not seed:
        return []
    need = max(2, len(seed) - 1)
    out = []
    for i in range(len(src) - len(seed) + 1):
        run = sum(1 for j, t in enumerate(seed) if src[i + j] == t)
        if run >= need:
            out.append(i)
    return out


# "Jon Stewart: he gave me..." - a speaker label the model prepended to an
# otherwise verbatim quote. The record carries the speaker as a separate
# annotation, never inline, so the label is not in the word stream: its tokens
# cannot anchor, the quote falls through to the unbounded global match below, and
# the span runs from the label's first occurrence anywhere in the record to the
# real content. Measured on jon-stewart: six haiku claims all pinned to word 703
# (the first "jon stewart" in the transcript) with ends up to 3h11m later.
_SPEAKER_PREFIX = re.compile(r"^[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){0,3}:\s+")

# How far a globally-matched span may exceed the quote before it is rejected as
# degenerate. A quote can legitimately span more source words than it has tokens -
# elision, stripped annotations, normalisation - but not by an order of magnitude.
_MAX_GLOBAL_SPAN_RATIO = 4.0
_MIN_GLOBAL_SPAN_SLACK = 40


def _strip_speaker_prefix(quote: str) -> str:
    return _SPEAKER_PREFIX.sub("", quote, count=1)


def _score_window(qt: list[str], src: list[str], times: list[float], i: int):
    """Coverage + (start, end) seconds for the quote aligned in a window at i."""
    slack = max(4, len(qt) // 4)
    window = src[i : i + len(qt) + slack]
    blocks = [
        b
        for b in SequenceMatcher(None, window, qt, autojunk=False).get_matching_blocks()
        if b.size > 0
    ]
    if not blocks:
        return None
    coverage = sum(b.size for b in blocks) / len(qt)
    first = min(blocks, key=lambda b: b.b)
    last = max(blocks, key=lambda b: b.b + b.size)
    start = times[i + first.a]
    end = times[min(i + last.a + last.size - 1, len(times) - 1)]
    return coverage, start, end


def align_quote(
    quote: str, words: list[str], times: list[float], resolution: str
) -> AlignResult | None:
    """Recover (start, end, coverage) for a quote against a word stream.

    Returns None if the quote is empty, the stream is empty, or no candidate
    clears the minimum coverage (the quote is not verbatim-present).
    """
    qt = tokenise(_strip_speaker_prefix(quote))
    if not qt or not words:
        return None

    cands = _candidate_starts(qt, words)
    scored = []
    for i in cands:
        s = _score_window(qt, words, times, i)
        if s:
            scored.append(s)

    if not scored:
        # No leading-token anchor matched (heavily normalised opening); fall back
        # to a single global match.
        blocks = [
            b
            for b in SequenceMatcher(
                None, words, qt, autojunk=False
            ).get_matching_blocks()
            if b.size > 0
        ]
        if not blocks:
            return None
        coverage = sum(b.size for b in blocks) / len(qt)
        first = min(blocks, key=lambda b: b.b)
        last = max(blocks, key=lambda b: b.b + b.size)
        # GUARD. Only this branch can produce a degenerate span: _score_window is
        # bounded to len(qt) + slack, so a windowed match is proportionate by
        # construction, while this fallback matches across the WHOLE stream and
        # will happily join a stray leading token to content an hour away. An
        # unalignable quote must stay unaligned - the claim then keeps whatever
        # the model wrote, which is honest - rather than acquire a confident span
        # covering half the record that no reviewer can check.
        span_words = (last.a + last.size) - first.a
        if span_words > max(
            len(qt) * _MAX_GLOBAL_SPAN_RATIO, len(qt) + _MIN_GLOBAL_SPAN_SLACK
        ):
            return None
        scored = [(coverage, times[first.a], times[last.a + last.size - 1])]

    scored.sort(key=lambda x: -x[0])
    best_cov, best_start, best_end = scored[0]
    if best_cov < _MIN_COVERAGE:
        return None

    ambiguous = any(
        cov >= best_cov - _AMBIG_COVERAGE_BAND
        and abs(start - best_start) > _AMBIG_GAP_S
        for cov, start, _ in scored[1:]
    )
    return AlignResult(
        start=round(best_start, 2),
        end=round(best_end, 2),
        coverage=round(best_cov, 3),
        resolution=resolution,
        ambiguous=ambiguous,
    )


def _set_location(claim: dict, value: str) -> None:
    """Write the canonical span to whichever location key this claim stage uses.

    In flight from the extractor the field is `location_in_record`; a written
    digest calls it `location`. Setting only one is a silent no-op at the other
    stage - the normaliser reported 10/10 aligned while the digest on disk kept
    the model's invented string, because the value went into a key the writer
    does not read. Set whichever exist, defaulting to the in-flight name.
    """
    if "location" in claim:
        claim["location"] = value
    if "location_in_record" in claim or "location" not in claim:
        claim["location_in_record"] = value


def _quote_of(claim: dict) -> str:
    """The claim's verbatim quote, whatever stage of the pipeline it is at.

    In-flight from the extractor the field is `original_excerpt`; once written to
    a digest it is `quote`. Reading only one silently yields "" and every claim
    reports as unalignable - which is exactly what happened: a normalisation wired
    into extraction scored 0/12 aligned because it was reading the post-write name
    of a pre-write field.
    """
    return (claim.get("quote") or claim.get("original_excerpt") or "").strip()


def seconds_to_timecode(seconds: float) -> str:
    """Seconds to the canonical HH:MM:SS.d timecode."""
    tenths = int(round(max(seconds, 0.0) * 10))
    whole, d = divmod(tenths, 10)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{d}"


def normalise_claim_locations(
    claims: list[dict], words: list[str], times: list[float]
) -> dict:
    """Rewrite every claim's `location` to a canonical HH:MM:SS.d range.

    A model left to write `location` itself produces whatever axis it feels like
    per chunk - bare seconds, timecodes, even source LINE numbers - so the same
    passage lands on different axes in different variants and the two cannot be
    clustered against each other. The quote, however, is verbatim, so the timing
    is recoverable deterministically: align the quote to the word stream and take
    the span. No model, no spend, and the answer is the same every run.

    A claim whose quote will not align (paraphrased, or not verbatim-present)
    keeps its original location and is counted as unaligned - fabricating a span
    would be worse than admitting we do not have one.
    """
    stats = {"aligned": 0, "unaligned": 0, "ambiguous": 0, "total": len(claims)}
    for claim in claims:
        result = align_quote(_quote_of(claim), words, times, "word")
        if result is None:
            stats["unaligned"] += 1
            continue
        _set_location(
            claim,
            f"{seconds_to_timecode(result.start)}-{seconds_to_timecode(result.end)}",
        )
        stats["aligned"] += 1
        if result.ambiguous:
            stats["ambiguous"] += 1
    return stats


def words_from_text(body: str) -> tuple[list[str], list[float]]:
    """(words, per-word CHARACTER OFFSET) from any untimed body.

    align_quote's second array is just "a number per word" - for a transcript
    those numbers are seconds, and nothing in the scoring cares. Feed it
    character offsets and it returns character offsets, so one aligner
    canonicalises location for timed and untimed records alike.

    This exists because a model asked to describe WHERE a claim came from
    invents its own notation, and two models never invent the same one. On real
    output: haiku wrote "11" where sonnet wrote "line 11"; haiku wrote
    "file_page: 1" where sonnet wrote "file_page 1, printed_page 5" - the same
    line and the same page, sharing not one location string. Anything grouping
    claims by that string sees two models that never once agree, which is not a
    disagreement about the record, it is a disagreement about formatting.
    """
    words: list[str] = []
    offsets: list[float] = []
    for m in _WORD.finditer((body or "").lower()):
        words.append(m.group())
        offsets.append(float(m.start()))
    return words, offsets


_CHAPTER_MARKER = re.compile(r"<!--\s*chapter:\s*([A-Za-z0-9]+)\s*-->")


def chapter_spans(body: str) -> list[tuple[str, int, int]]:
    """(label, start, end) for each chapter region in a body, in order.

    Empty when the body has no chapter markers, which is every record type but
    ebooks (and the occasional structured PDF).
    """
    marks = [(m.group(1), m.end()) for m in _CHAPTER_MARKER.finditer(body)]
    if not marks:
        return []
    out = []
    for i, (label, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(body)
        out.append((label, start, end))
    return out


def offsets_to_span(
    start: float, end: float, chapters: list[tuple[str, int, int]] | None = None
) -> str:
    """The canonical untimed location.

    CHAPTER-RELATIVE where the record has chapters (``ch3:1240-1310``), a bare
    pre-digest character span otherwise (``char:1240-1310``).

    A global character offset is only meaningful against one exact pre-digest: it
    shifts if the handler re-extracts the source, and it shifts again on any
    PREP_VERSION bump, because both change the text the offsets index into. For a
    600KB book that is the whole location scheme dying at the next prep change.
    A chapter label survives both - it comes from the source's own structure, not
    from our processing - so only the offset WITHIN the chapter has to be
    recomputed, and a reviewer looking for the quote has a chapter to open rather
    than a character count into an 800KB file.
    """
    s, e = int(start), int(end)
    for label, c_start, c_end in chapters or []:
        if c_start <= s < c_end:
            return f"ch{label}:{s - c_start}-{max(e - c_start, s - c_start)}"
    return f"char:{s}-{e}"


def normalise_untimed_locations(claims: list[dict], body: str) -> dict:
    """Rewrite every claim's location to a canonical char span, from its quote.

    Same contract as normalise_claim_locations for timed records, and the same
    reason: the quote is verbatim, so its position is recoverable deterministically
    and identically for every model. A claim whose quote will not align keeps its
    original location rather than getting a fabricated one.
    """
    words, offsets = words_from_text(body)
    chapters = chapter_spans(body)
    stats = {"aligned": 0, "unaligned": 0, "ambiguous": 0, "total": len(claims)}
    for claim in claims:
        r = align_quote(_quote_of(claim), words, offsets, "char")
        if r is None:
            stats["unaligned"] += 1
            continue
        _set_location(claim, offsets_to_span(r.start, r.end, chapters))
        stats["aligned"] += 1
        if r.ambiguous:
            stats["ambiguous"] += 1
    return stats
