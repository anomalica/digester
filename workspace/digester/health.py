"""Post-digest health checks: conditions a successful exit code cannot see.

A run that exits 0 and writes a file looks identical to a good extraction.
Every check here turns one such silent success into a visible condition.

--- CACHE PREFIX ---

Prompt caching only pays when the STABLE part of a call comes before the variable
part. The claims pass assembles `prompt` (carrying the fixed node directory) and
then the chunk text, so the directory sits in the cacheable prefix and is read
back on every chunk rather than rewritten.

If that order is ever reversed - by a refactor, a new field appended in the wrong
place, a template edit - the prefix collapses and every call rewrites what it
used to read. Nothing else changes: the extraction is correct, the claims are the
same, every test passes. Only the cost characteristic moves, which is exactly the
class of failure that goes unnoticed.

`cache_read / cache_write` detects it directly. A healthy record reads back
roughly what it wrote (ratio near or above 1); a collapsed prefix drives the
ratio toward zero because nothing is ever read.

Measured baseline on books, 2026-07-31:

    Invisible College   1.23      Remote Viewing Secrets  0.96
    Conference Report   1.13      Hair of the Alien       0.81
    Messengers          1.03      Imminent                1.01

All near 1.0, which is itself a weak result - the cache roughly breaks even on
books rather than winning - and consistent with the CLI's own ~32K-per-call
scaffolding dominating the payload. The threshold here is set to catch a
COLLAPSE, not to police that weakness.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import yaml

# A prefix that has genuinely collapsed reads back almost nothing. The observed
# floor across real books is 0.81, so this sits well below it: it flags a broken
# prefix, never a merely inefficient one.
COLLAPSE_RATIO = 0.30

# The ratio is STRUCTURALLY bounded by call count, so the check is only diagnostic
# once the prefix has enough calls to amortise over. Each pass writes its prefix on
# its first call and reads nothing back, so those writes are a fixed cost divided by
# however many calls follow. Measured across the corpus:
#
#     1 call   median 0.28        6 calls   median 0.65
#     2 calls  median 0.32       11+ calls  0.85 - 1.23
#     3 calls  median 0.37 (range 0.03 - 1.20)
#
# The 0.30 threshold was measured on BOOKS, which run 34-120 calls, and set below
# their observed floor of 0.81. Applied to a 3-call video it sits inside the normal
# distribution rather than below it, and duly flagged 5 of 15 three-call records as
# broken when their population median is 0.37. Same error as the corpus-wide yield
# median: a threshold calibrated on one population applied to another.
#
# Raised to 6, where the observed minimum is 0.54 and the threshold is once again
# below every genuine record. Nothing is lost by not checking short records: a
# collapsed prefix is a code-level fault that affects EVERY record, so the ~25 with
# enough calls to judge will show it. This is a corpus canary, not a per-record
# verdict.
MIN_CALLS = 6


def ratios(digests_dir: Path) -> list[dict]:
    """cache read/write ratio per digest, newest field set only."""
    out = []
    for f in sorted(digests_dir.glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text())
        except (OSError, yaml.YAMLError):
            continue
        usage = (d.get("ai_usage") or [{}])[0]
        t = usage.get("tokens") or {}
        read, write, calls = (
            t.get("cache_read"),
            t.get("cache_write"),
            t.get("calls"),
        )
        if not write:  # pre-dates the breakdown fields, or no caching happened
            continue
        out.append(
            {
                "digest": f.stem,
                "read": read or 0,
                "write": write,
                "calls": calls or 0,
                "ratio": (read or 0) / write,
            }
        )
    return out


def collapsed(digests_dir: Path, threshold: float = COLLAPSE_RATIO) -> list[dict]:
    """Digests whose cacheable prefix looks broken rather than merely small."""
    return [
        r
        for r in ratios(digests_dir)
        if r["ratio"] < threshold and r["calls"] >= MIN_CALLS
    ]


# --- YIELD FLOOR ---
#
# hair-of-the-alien produced 2 domain claims from 619KB, exited 0, wrote a digest,
# and sat in the corpus for days counted as a completed book - inflating every
# corpus figure quoted about it. Nothing flagged it because a successful exit and
# a written file are indistinguishable from a good extraction.
#
# Claim density varies legitimately by medium - spoken-word video runs 0.54-0.63
# claims/KB against 4.39 for a dense book - so an absolute floor would either miss
# the failure or condemn every transcript.
#
# The factor was 0.2 for as long as ONE median spanned both populations, and it had
# to be: a single floor cannot exceed 0.23 of the corpus median without condemning
# real video, because the sparsest genuine record anywhere sits at 0.55 claims/KB
# against a corpus median of 2.40. That ceiling was imposed by the 3.5x gap between
# documents and spoken media, not by anything about how far a book can fall.
#
# So the guard tolerated a book losing FOUR FIFTHS of its content. Grouping the
# median per type removes the constraint that forced it: each floor is now bounded
# only by its OWN population's spread, which is far tighter - the sparsest real
# record of each type sits at 0.68 (ebook), 0.74 (pdf), 0.80 (video) of its median.
#
# Measured across 42 records, no genuine record is flagged at any factor up to
# 0.60; the first false positive appears at 0.68. 0.35 is set well inside that:
# it catches a 65% content loss instead of an 80% one, while every real record
# still sits 1.9-2.3x above its own floor. Deliberately conservative while video
# is only n=17 and the medians are still firming up.
YIELD_FLOOR_FRACTION = 0.35
YIELD_MIN_KB = 20


def claim_yields(digests_dir: Path, store_dir: Path) -> list[dict]:
    """claims-per-source-KB per digest, for records large enough to judge."""
    out = []
    for f in sorted(digests_dir.glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text())
        except (OSError, yaml.YAMLError):
            continue
        h = ((d.get("record") or {}).get("content_hash") or "").split(":")[-1]
        src = 0
        for c in (
            store_dir / f"{h}.v2.md",
            store_dir / f"{h}.md",
            store_dir / "v1" / f"{h}.v2.md",
            store_dir / "v1" / f"{h}.md",
        ):
            if c.exists():
                src = c.stat().st_size
                break
        kb = src / 1000
        if kb < YIELD_MIN_KB:
            continue
        n = len((d.get("domain_claims") or [])) + len(
            (d.get("infrastructure_claims") or [])
        )
        out.append(
            {
                "digest": f.stem,
                "kb": kb,
                "claims": n,
                "per_kb": n / kb,
                "medium": (d.get("record") or {}).get("medium"),
            }
        )
    return out


# Minimum records of a type before that type gets its own median. Below it the
# type inherits a broader figure rather than being judged on a sample too small
# to have a norm.
YIELD_MIN_PER_TYPE = 5

# What a thin type inherits FROM. Not the corpus: the corpus is a mix of these two
# populations, so falling back to it reintroduces the exact averaging the per-type
# split exists to remove - and reintroduces it precisely for the types with too
# little data to notice. Measured, the split is clean at ~3.5x: documents run
# ~2.85 claims/KB, spoken media ~0.80, because transcripts carry filler,
# repetition and back-and-forth where documents are dense with assertions.
#
# It matters in BOTH directions. With audio at n=2 the corpus fallback judges a
# transcript against a document-weighted floor - roughly 3x too strict, so a
# perfectly normal audio record reads as a failed extraction. With web at n=2 it
# judges a dense document against a floor being dragged down by video - too
# lenient, and lenience is the failure this guard exists to prevent.
MEDIA_FAMILIES = {
    "video": "spoken",
    "audio": "spoken",
    "ebook": "document",
    "pdf": "document",
    "web": "document",
    "email": "document",
}


def low_yield(digests_dir: Path, store_dir: Path) -> list[dict]:
    """Digests whose claim yield is so far below the norm FOR THEIR SOURCE TYPE
    that the extraction probably failed despite exiting successfully.

    Per type, not corpus-wide, because the two populations differ 3.5x: documents
    run ~2.85 claims/KB and spoken media ~0.80, since transcripts carry filler and
    repetition where documents are dense with assertions. That is source type, not
    quality - old-prompt videos sit inside the new-prompt video range, so it is not
    a prompt effect either.

    A single median over both is a weighted average, so it MIGRATES as the mix
    changes - and with 108 video records queued it migrates downward, taking the
    floor with it. A guard that gets less sensitive as more data arrives is the
    wrong shape: an ebook that lost 80% of its content is already missed today,
    and one that lost 90% stops being caught once the corpus tilts. That is the
    exact failure this guard exists for.
    """
    rows = claim_yields(digests_dir, store_dir)
    if len(rows) < 5:
        return []
    corpus_med = statistics.median(r["per_kb"] for r in rows)
    by_type: dict[str, list[float]] = {}
    by_family: dict[str, list[float]] = {}
    for r in rows:
        t = r.get("medium") or "?"
        by_type.setdefault(t, []).append(r["per_kb"])
        fam = MEDIA_FAMILIES.get(t)
        if fam:
            by_family.setdefault(fam, []).append(r["per_kb"])
    out = []
    for r in rows:
        t = r.get("medium") or "?"
        for vals, basis in (
            (by_type.get(t, []), t),
            (by_family.get(MEDIA_FAMILIES.get(t) or "", []), MEDIA_FAMILIES.get(t)),
            ([corpus_med], "corpus"),
        ):
            if len(vals) >= YIELD_MIN_PER_TYPE or basis == "corpus":
                med = statistics.median(vals)
                break
        if r["per_kb"] < med * YIELD_FLOOR_FRACTION:
            out.append({**r, "floor_basis": basis, "type_median": round(med, 2)})
    return out


# --- PRE-DIGEST SURVIVAL ---
#
# hair-of-the-alien reaches the model as 1,023 characters of a 88,384-character
# body: four irrelevant regions remove 98.8% of it. The extraction then behaves
# correctly on the kilobyte it is given, exits 0, and writes a digest that reads
# like a completed book.
#
# The yield floor catches that case only because it is extreme. A book marked 40%
# irrelevant would produce a plausible claim count and never trip it, while
# silently omitting two fifths of the source. This measures the thing directly:
# how much of the body survives materialise. Across 17 books the marked ones sit
# at 93.5-97% and the unmarked at 98-100%, so a real apparatus pass is visible
# and harmless here; anything below half is not apparatus.
SURVIVAL_FLOOR = 0.5


def pre_digest_survival(record_md: Path) -> float | None:
    """Fraction of a record's body that survives into the pre-digest."""
    from anomalica_common.pre_digest import materialise

    from digester.record_parser import parse_record

    from anomalica_common.pre_digest import strip_word_timestamps

    try:
        body = parse_record(record_md.resolve().read_text(errors="replace")).body
    except (OSError, ValueError):
        return None
    if not body:
        return None
    # Measure against the body with WORD TIMESTAMPS already removed. A record/2
    # transcript is ~65% {{t:...}} tokens by character, and materialise strips
    # them correctly - so a raw ratio puts every transcript at ~30% and a flat
    # floor flags 129 records that are all fine. Comparing like with like isolates
    # what the ANNOTATIONS remove from what the transcript FORMAT removes.
    baseline = strip_word_timestamps(body)
    if not baseline:
        return None
    return len(materialise(body)) / len(baseline)


def over_marked(records_dir: Path, floor: float = SURVIVAL_FLOOR) -> list[dict]:
    """Records whose annotations remove so much body that a digest built from
    them would misrepresent the source."""
    out = []
    for p in sorted(records_dir.glob("*.md")):
        frac = pre_digest_survival(p)
        if frac is not None and frac < floor:
            out.append({"record": p.name, "survives": round(frac, 4)})
    return out


# --- UNMAPPED RECORD FIELDS ---
#
# The digest's record block is an ALLOW-LIST, and correctly so: the digest is a
# locked interchange schema, and passing a record's whole frontmatter through
# would publish unspecified fields into it - some large, some copyright-bearing.
#
# But an allow-list drops a NEW upstream field silently. That has now happened at
# three boundaries in one day: the assimilator's parser dropped five record-block
# fields, the ingester's chunked merge dropped document-level markings, and this
# emitter dropped `release` hours after the ingester built it. Each looked like an
# empty column from a producer that had not started emitting yet.
#
# So the fix for an allow-list is not a longer list - it is knowing when the list
# has fallen behind. This reports frontmatter a record carries that no digest
# field maps, which turns the next omission into a visible condition.
MAPPED_RECORD_FIELDS = frozenset(
    {
        "title",
        "publisher",
        "date_published",
        "date",
        "source_type",
        "duration",
        "content_hash",
        "processing",
        "creators",
        "authors",
        "release",
        "provenance",
        "classification",
        "supersedes",
        "source_id",
        "source_url",
        # Ruled in 2026-08-01 after the detector's first run named eight
        # unmapped fields. speakers is the SOURCE's own roster, independent of
        # what the extractor found - a claim attributed to someone absent from
        # it, or a roster member yielding nothing, is a quality signal that
        # cannot be computed at all without it. pages bounds location validity.
        # review_carryover records human attention to a superseded version.
        "speakers",
        "pages",
        "fetched_url",
        "description",
        "review_carryover",
    }
)

# Deliberately NOT carried: acquisition detail, storage detail, or overlay state
# that a digest consumer has no use for. Listed rather than merely absent, so the
# report distinguishes "decided against" from "nobody has looked".
UNWANTED_RECORD_FIELDS = frozenset(
    {
        "schema",
        "archived_ext",
        "quality",
        "word_timestamps",
        "overlay_next_id",
        "source_file",
        "date_accessed",
        "date_extracted",
        "snapshots",
        "source_hash",
        "copyright",
        "superseded_by",
        "superseded_reason",
        # media is storage accounting and says nothing about claims. copyright is
        # access-control state whose authority is the ingest record - a copy in a
        # public artefact would be a staler second source of truth for an access
        # decision, so it deliberately has one home. document_type and email are
        # single-record and not worth schema surface.
        "media",
        "document_type",
        "email",
    }
)


def unmapped_record_fields(records_dir: Path) -> dict[str, int]:
    """Frontmatter keys carried by records that no digest field maps.

    Excludes those explicitly decided against. A non-empty result means an
    upstream producer has added something the digest does not yet carry.
    """
    import yaml

    counts: dict[str, int] = {}
    for p in sorted(records_dir.glob("*.md")):
        t = p.resolve()
        if not t.exists():
            continue
        raw = t.read_text(errors="replace")
        if not raw.startswith("---"):
            continue
        try:
            fm = yaml.safe_load(raw.split("---", 2)[1])
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        for k in fm:
            if k in MAPPED_RECORD_FIELDS or k in UNWANTED_RECORD_FIELDS:
                continue
            counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
