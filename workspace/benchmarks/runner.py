"""Golden-set benchmark scorer.

Scores an extraction output against a hand-curated golden.yaml and appends a
scorecard to history.jsonl. Scoring an already-produced extraction costs
nothing; only generating a fresh extraction spends credits (not done here -
feed it the JSON from a compare_discovery run or a digester extract).

Metrics:
  recall    = matched must_find / total must_find        (alias-tolerant)
  precision = 1 - (deny matches / total extracted)        (objective: only
              KNOWN noise counts against you; uncurated extractions are
              reported, not penalised, because the golden set is not claimed
              exhaustive for every type)
Also reported: per-type recall, the list of must_find items missed, the
deny items hit (false positives), and the uncurated extractions (neither
must_find nor deny - for human eyeballing / golden-set growth).

Usage:
  python benchmarks/runner.py <golden.yaml> <extraction.json> [--label X]

`extraction.json` is the compare_discovery cell format:
  {"found": {"people": [{"name": ...}], "organisations": [...], ...},
   "total_cost_usd": ..., "total_elapsed_s": ..., "total_output_tokens": ...}
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import yaml

# The discovery harness emits plural type keys; the digester / golden use
# singular. Canonicalise everything to singular before scoring.
_TYPE_CANON = {
    "people": "person",
    "persons": "person",
    "organisations": "organisation",
    "organizations": "organisation",
    "places": "place",
    "events": "event",
    "matters": "matter",
    "objects": "object",
    "documents": "document",
    "concepts": "concept",
    "records": "record",
}


def canon_type(t: str) -> str:
    return _TYPE_CANON.get(t, t)


_RANK_PREFIX = re.compile(
    r"^(dr|mr|mrs|ms|prof|professor|president|senator|sir|the)\s+",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalise(name: str) -> str:
    """Lowercase, strip punctuation, rank/title prefixes, collapse whitespace."""
    n = name.strip().lower()
    n = _PUNCT.sub(" ", n)
    n = _WS.sub(" ", n).strip()
    # strip a leading rank/title token (repeat: "dr the" etc.)
    prev = None
    while prev != n:
        prev = n
        n = _RANK_PREFIX.sub("", n).strip()
    return n


def name_variants(name: str) -> set[str]:
    """Normalised forms to match on, including the "Last, First" reversal."""
    v = {normalise(name)}
    if "," in name:
        a, b = name.split(",", 1)
        v.add(normalise(f"{b.strip()} {a.strip()}"))
    return {x for x in v if x}


def build_matcher(golden_entities: list[dict]) -> list[tuple[set[str], str]]:
    """Return [(accepted_normalised_forms, canonical), ...]."""
    out = []
    for ent in golden_entities:
        forms: set[str] = set()
        forms |= name_variants(ent["canonical"])
        for alias in ent.get("aliases") or []:
            forms |= name_variants(alias)
        out.append((forms, ent["canonical"]))
    return out


def match(extracted_name: str, matcher: list[tuple[set[str], str]]) -> str | None:
    """Return the canonical golden name an extraction matches, or None."""
    forms = name_variants(extracted_name)
    for accepted, canonical in matcher:
        if forms & accepted:
            return canonical
    return None


# Types whose names are phrased many ways (a patent/paper/event/idea has no
# single canonical surface form). For these we add a token-overlap fallback
# AFTER exact/alias match fails. Strict types (person/place/object/org/matter)
# do NOT get loose matching - there exact-ish is correct and loose would
# inflate recall with false matches.
LOOSE_TYPES = {"event", "document", "concept"}

_STOP = {
    "the",
    "a",
    "an",
    "of",
    "for",
    "to",
    "in",
    "on",
    "and",
    "or",
    "by",
    "with",
    "re",
    "at",
    "as",
    "from",
    "about",
    "into",
}


def _content_tokens(name: str) -> set[str]:
    """Significant tokens for loose matching: normalised, stopwords removed.

    4-digit years are kept (they are distinctive for events/documents).
    Tokens shorter than 3 chars are dropped unless they are years.
    """
    toks = set()
    for raw in normalise(name).split():
        if raw in _STOP:
            continue
        if raw.isdigit() and len(raw) == 4:
            toks.add(raw)
        elif len(raw) >= 3:
            toks.add(raw)
    return toks


def build_loose_matcher(
    golden_entities: list[dict],
) -> list[tuple[set[str], str]]:
    """[(union_of_content_tokens_over_canonical+aliases, canonical), ...]."""
    out = []
    for ent in golden_entities:
        toks = _content_tokens(ent["canonical"])
        for alias in ent.get("aliases") or []:
            toks |= _content_tokens(alias)
        out.append((toks, ent["canonical"]))
    return out


def loose_match(
    extracted_name: str, loose_matcher: list[tuple[set[str], str]]
) -> str | None:
    """Token-overlap fallback for LOOSE_TYPES.

    Matches if the extraction shares >=2 significant tokens with a golden
    entity AND covers >=60% of that entity's distinctive tokens. Conservative
    on purpose - it must contain most of the golden's key words, so it
    rewards rephrasing without rewarding vaguely-related noise.
    """
    ext = _content_tokens(extracted_name)
    if not ext:
        return None
    best = None
    best_cov = 0.0
    for toks, canonical in loose_matcher:
        if not toks:
            continue
        shared = ext & toks
        if len(shared) < 2:
            continue
        coverage = len(shared) / len(toks)
        if coverage >= 0.6 and coverage > best_cov:
            best, best_cov = canonical, coverage
    return best


def score(golden: dict, extraction: dict) -> dict:
    raw_found = extraction.get("found", {})
    found: dict[str, list] = {}
    for t, lst in raw_found.items():
        found.setdefault(canon_type(t), []).extend(lst)
    must_raw = golden.get("must_find", {}) or {}
    must = {canon_type(t): v for t, v in must_raw.items()}
    deny_raw = golden.get("deny", {}) or {}
    deny = {canon_type(t): v for t, v in deny_raw.items()}

    per_type = {}
    total_must = 0
    total_matched = 0
    total_extracted = 0
    total_deny_hits = 0
    missed_all = {}
    deny_hits_all = {}
    uncurated_all = {}

    types = sorted(set(list(must.keys()) + list(found.keys())))
    for t in types:
        golden_ents = must.get(t, []) or []
        matcher = build_matcher(golden_ents)
        loose = build_loose_matcher(golden_ents) if t in LOOSE_TYPES else None
        deny_norm = {normalise(d) for d in (deny.get(t, []) or [])}

        extracted = [e.get("name", "") for e in found.get(t, []) if e.get("name")]
        total_extracted += len(extracted)

        matched_canonicals = set()
        deny_hits = []
        uncurated = []
        for name in extracted:
            canonical = match(name, matcher)
            if not canonical and loose is not None:
                canonical = loose_match(name, loose)
            if canonical:
                matched_canonicals.add(canonical)
                continue
            if name_variants(name) & {n for n in deny_norm}:
                deny_hits.append(name)
                continue
            uncurated.append(name)

        n_must = len(golden_ents)
        n_matched = len(matched_canonicals)
        total_must += n_must
        total_matched += n_matched
        total_deny_hits += len(deny_hits)

        missed = [
            g["canonical"]
            for g in golden_ents
            if g["canonical"] not in matched_canonicals
        ]
        per_type[t] = {
            "must_find": n_must,
            "matched": n_matched,
            "recall": round(n_matched / n_must, 3) if n_must else None,
            "extracted": len(extracted),
            "deny_hits": len(deny_hits),
            "uncurated": len(uncurated),
        }
        if missed:
            missed_all[t] = missed
        if deny_hits:
            deny_hits_all[t] = deny_hits
        if uncurated:
            uncurated_all[t] = uncurated

    recall = round(total_matched / total_must, 3) if total_must else None
    precision = (
        round(1 - total_deny_hits / total_extracted, 3) if total_extracted else None
    )

    return {
        "recall": recall,
        "precision": precision,
        "total_must_find": total_must,
        "total_matched": total_matched,
        "total_extracted": total_extracted,
        "total_deny_hits": total_deny_hits,
        "per_type": per_type,
        "missed": missed_all,
        "deny_hits": deny_hits_all,
        "uncurated": uncurated_all,
        "cost_usd": extraction.get("total_cost_usd"),
        "wall_seconds": extraction.get("total_elapsed_s"),
        "output_tokens": extraction.get("total_output_tokens"),
        "calls": extraction.get("total_calls"),
    }


def check_regressions(scorecard: dict, expected: dict) -> list[str]:
    flags = []
    r, p = scorecard["recall"], scorecard["precision"]
    if r is not None and r < expected.get("recall_min", 0):
        flags.append(f"recall {r} < {expected['recall_min']}")
    if p is not None and p < expected.get("precision_min", 0):
        flags.append(f"precision {p} < {expected['precision_min']}")
    cost = scorecard.get("cost_usd")
    if cost is not None and cost > expected.get("cost_usd_max", 1e9):
        flags.append(f"cost ${cost} > ${expected['cost_usd_max']}")
    secs = scorecard.get("wall_seconds")
    if secs is not None and secs > expected.get("wall_seconds_max", 1e9):
        flags.append(f"time {secs}s > {expected['wall_seconds_max']}s")
    return flags


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: runner.py <golden.yaml> <extraction.json> [--label X]",
            file=sys.stderr,
        )
        sys.exit(1)
    golden = yaml.safe_load(Path(sys.argv[1]).read_text())
    extraction = json.loads(Path(sys.argv[2]).read_text())
    label = "unlabelled"
    if "--label" in sys.argv:
        label = sys.argv[sys.argv.index("--label") + 1]

    scorecard = score(golden, extraction)
    flags = check_regressions(scorecard, golden.get("expected", {}))

    print(f"\n=== {label} ===")
    print(
        f"recall    {scorecard['recall']}  ({scorecard['total_matched']}/{scorecard['total_must_find']} must-find)"
    )
    print(
        f"precision {scorecard['precision']}  ({scorecard['total_deny_hits']} known-noise of {scorecard['total_extracted']} extracted)"
    )
    print(
        f"cost ${scorecard['cost_usd']}  time {scorecard['wall_seconds']}s  calls {scorecard['calls']}"
    )
    print("\nper-type recall:")
    for t, d in scorecard["per_type"].items():
        print(
            f"  {t:14s} {str(d['recall']):>5}  matched {d['matched']}/{d['must_find']}, "
            f"extracted {d['extracted']}, noise {d['deny_hits']}, uncurated {d['uncurated']}"
        )
    if scorecard["missed"]:
        print("\nMISSED must-find:")
        for t, items in scorecard["missed"].items():
            print(f"  {t}: {items}")
    if scorecard["deny_hits"]:
        print("\nNOISE extracted (precision hits):")
        for t, items in scorecard["deny_hits"].items():
            print(f"  {t}: {items}")
    if flags:
        print(f"\n*** REGRESSION FLAGS: {flags}")
    else:
        print("\nwithin expected bands")

    history = Path(sys.argv[1]).parent / "history.jsonl"
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "label": label,
        "recall": scorecard["recall"],
        "precision": scorecard["precision"],
        "cost_usd": scorecard["cost_usd"],
        "wall_seconds": scorecard["wall_seconds"],
        "calls": scorecard["calls"],
        "per_type": {t: d["recall"] for t, d in scorecard["per_type"].items()},
        "flags": flags,
    }
    with history.open("a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"\nappended scorecard to {history}")


if __name__ == "__main__":
    main()
