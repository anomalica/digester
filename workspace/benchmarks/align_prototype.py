#!/usr/bin/env python3
"""Zero-spend prototype: can post-hoc fuzzy alignment recover where a claim's
quote sits in the source, well enough to assign a timestamp?

The v2 plan strips timestamps before extraction (clean text in), then re-aligns
the model's emitted quotes/claims back to source timing afterwards. The hard,
uncertain part (concern 2) is that the model NORMALISES text - British spelling,
expanded contractions, "u.s" -> "U.S.", merged Q&A turns - so an emitted quote
is rarely a verbatim substring of the source.

This tests the matching in isolation, with no metered run, by reusing the
existing navy digest: each claim already carries a model-assigned `location`
(the inline timestamp the extractor chose). We strip that, fuzzy-align the quote
back to the source word stream ourselves, and measure how close our recovered
timestamp lands to the model's own choice. Good agreement => post-hoc alignment
can replace inline location assignment. Poor/ambiguous => anchors are justified.
"""

from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SOURCE = (
    ROOT
    / "ingests/by-name/2021-05-17-video-navy-pilots-describe-encounters-with-ufos.md"
)
DIGEST = (
    ROOT / "digests/2021-05-17-video-navy-pilots-describe-encounters-with-ufos.yaml"
)

_TS_LINE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d)\s+(.*)$")
_WORD = re.compile(r"[a-z0-9]+")


def ts_to_sec(ts: str) -> float | None:
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})\.(\d)", ts.strip())
    if not m:
        return None
    h, mi, s, d = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + d / 10


def tokenise(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def load_source_words(path: Path) -> tuple[list[str], list[float]]:
    """Flat word stream + per-word timestamp (the word's sentence start)."""
    body = path.read_text().split("---", 2)[2]
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


def align(quote_tokens: list[str], src: list[str], src_t: list[float]):
    """Return (recovered_start_sec, coverage, ambiguous).

    coverage = fraction of quote tokens matched in the best alignment.
    ambiguous = a second, disjoint window matches nearly as well (the
    "'I know' appears dozens of times" failure mode).
    """
    if not quote_tokens:
        return None, 0.0, False
    sm = SequenceMatcher(None, src, quote_tokens, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
    if not blocks:
        return None, 0.0, False
    coverage = sum(b.size for b in blocks) / len(quote_tokens)
    # Recovered start = source position of the block covering the EARLIEST
    # quote token (smallest b), i.e. where the quote begins in the source.
    first = min(blocks, key=lambda b: b.b)
    start_sec = src_t[first.a]

    # Ambiguity probe: is there another start position, far from this one,
    # whose leading-token run is comparably long? Use the first 6 quote tokens
    # as an anchor and count distinct source positions where >=4 of them match
    # consecutively.
    head = quote_tokens[: min(6, len(quote_tokens))]
    hits = []
    for i in range(len(src) - len(head) + 1):
        run = sum(1 for j, t in enumerate(head) if src[i + j] == t)
        if run >= max(3, len(head) - 1):
            hits.append(i)
    far_hits = [i for i in hits if abs(src_t[i] - start_sec) > 5.0]
    ambiguous = len(far_hits) > 0
    return start_sec, coverage, ambiguous


_V2_RECORD = (
    ROOT
    / "ingests/store"
    / "2399fe4eebefe8e365c1af34d20bef2dc0cb74d8a451430e8fdd34be94fb1d54.v2.md"
)
_WORD_TOKEN = re.compile(r"\{\{t:([0-9.]+)\}\}([^{]*)")


def load_word_timestamps(path: Path) -> tuple[list[str], list[float], str]:
    """Parse a record/2 body into (words, per-word seconds, clean text).

    Strips the inline {{t:SEC}} tokens that precede each word - the noise the
    digester should never see - while keeping a parallel word->timestamp map
    for re-alignment.
    """
    body = path.read_text().split("---", 2)[2]
    words: list[str] = []
    times: list[float] = []
    for m in _WORD_TOKEN.finditer(body):
        t = float(m.group(1))
        for w in tokenise(m.group(2)):
            words.append(w)
            times.append(t)
    clean = re.sub(r"\{\{t:[0-9.]+\}\}", "", body)
    return words, times, clean


def demo_word_level() -> None:
    """Show the end-to-end word-level mechanism on a real record/2: strip,
    build the word->timestamp map, align a verbatim quote, read off timing."""
    if not _V2_RECORD.exists():
        return
    words, times, clean = load_word_timestamps(_V2_RECORD)
    body_with = _V2_RECORD.read_text().split("---", 2)[2]
    print("WORD-LEVEL MECHANISM on a record/2 (strip -> map -> align):")
    print(
        f"  {len(words):,} timestamped words; clean text {len(clean):,} chars "
        f"vs {len(body_with):,} with tokens "
        f"({100 * (len(body_with) - len(clean)) // len(body_with)}% was token noise)"
    )
    for phrase in ("lateral transition", "I know, Susan, what you asked for"):
        qt = tokenise(phrase)
        sm = SequenceMatcher(None, words, qt, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
        if not blocks:
            continue
        cov = sum(b.size for b in blocks) / len(qt)
        first = min(blocks, key=lambda b: b.b)
        last = max(blocks, key=lambda b: b.b + b.size)
        start, end = times[first.a], times[last.a + last.size - 1]
        print(
            f"  quote {phrase!r}: coverage={cov:.2f} -> "
            f"word-level {start:.2f}s-{end:.2f}s"
        )
    print()


def main() -> int:
    if not SOURCE.exists() or not DIGEST.exists():
        print(f"missing inputs:\n  {SOURCE}\n  {DIGEST}")
        return 1
    src, src_t = load_source_words(SOURCE)
    doc = yaml.safe_load(DIGEST.read_text())
    claims = (doc.get("domain_claims") or []) + (doc.get("infrastructure_claims") or [])

    rows = []
    for c in claims:
        quote = c.get("quote")
        loc = c.get("location")
        if not quote or not loc:
            continue
        model_sec = ts_to_sec(str(loc))
        if model_sec is None:
            continue
        rec_sec, cov, amb = align(tokenise(quote), src, src_t)
        if rec_sec is None:
            continue
        rows.append(
            {
                "delta": abs(rec_sec - model_sec),
                "coverage": cov,
                "ambiguous": amb,
                "model_sec": model_sec,
                "rec_sec": rec_sec,
                "quote": quote.replace("\n", " ")[:70],
            }
        )

    n = len(rows)
    if not n:
        print("no quote+location claims found")
        return 1

    def pct(pred):
        return 100 * sum(1 for r in rows if pred(r)) / n

    print(f"source words: {len(src):,}   quote-bearing claims aligned: {n}")
    print()
    print("AGREEMENT with the model's own inline location (delta in seconds):")
    print(f"  exact (<0.05s):     {pct(lambda r: r['delta'] < 0.05):5.1f}%")
    print(f"  within 2s:          {pct(lambda r: r['delta'] <= 2):5.1f}%")
    print(f"  within 5s:          {pct(lambda r: r['delta'] <= 5):5.1f}%")
    print(f"  within 10s:         {pct(lambda r: r['delta'] <= 10):5.1f}%")
    print(f"  off by >10s:        {pct(lambda r: r['delta'] > 10):5.1f}%")
    print()
    print("MATCH QUALITY (how verbatim the quote is vs source):")
    print(f"  coverage >=0.9:     {pct(lambda r: r['coverage'] >= 0.9):5.1f}%")
    print(f"  coverage 0.5-0.9:   {pct(lambda r: 0.5 <= r['coverage'] < 0.9):5.1f}%")
    print(f"  coverage <0.5:      {pct(lambda r: r['coverage'] < 0.5):5.1f}%")
    print(f"  flagged ambiguous:  {pct(lambda r: r['ambiguous']):5.1f}%")
    print()
    # The interesting tail: where unanchored alignment disagrees with the model.
    bad = sorted([r for r in rows if r["delta"] > 10], key=lambda r: -r["delta"])
    print(f"WORST DISAGREEMENTS (delta >10s): {len(bad)}")
    for r in bad[:12]:
        flag = " AMBIG" if r["ambiguous"] else ""
        print(
            f"  d={r['delta']:6.1f}s cov={r['coverage']:.2f}{flag}  "
            f"model={r['model_sec']:.1f} rec={r['rec_sec']:.1f}  {r['quote']!r}"
        )
    print()

    # The decisive comparison: align on the verbatim quote vs the paraphrased
    # text. The quote is near-verbatim (good); the paraphrase is normalised
    # (concern 2) and aligns far worse - so always align on the quote.
    print("ALIGN ON QUOTE vs PARAPHRASED TEXT (why the quote is the anchor):")
    for field in ("quote", "text"):
        frows = []
        for c in claims:
            v, loc = c.get(field), c.get("location")
            if not v or not loc:
                continue
            ms = ts_to_sec(str(loc))
            if ms is None:
                continue
            rs, cov, _ = align(tokenise(v), src, src_t)
            if rs is not None:
                frows.append((abs(rs - ms), cov))
        m = len(frows)
        hi = 100 * sum(1 for d, cov in frows if cov >= 0.9) / m
        w2 = 100 * sum(1 for d, cov in frows if d <= 2) / m
        print(f"  {field:6}: coverage>=0.9 {hi:5.1f}%   within 2s {w2:5.1f}%")
    print()

    demo_word_level()
    return 0


if __name__ == "__main__":
    sys.exit(main())
