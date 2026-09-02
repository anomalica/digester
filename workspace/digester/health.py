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

import hashlib
import json
import os
import statistics
from pathlib import Path

import yaml

try:  # libyaml is ~6.6x faster and a digest can reach 3.5MB
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - pure-python fallback
    _Loader = yaml.SafeLoader


def load_digests(digests_dir: Path) -> list[tuple[str, dict]]:
    """Every digest, parsed ONCE.

    claim_yields and collapsed each used to parse the whole corpus separately,
    so a health run paid for two full passes over the same ~57 files - 75
    seconds, almost all of it in the YAML parser rather than in any check.
    """
    out = []
    for f in sorted(digests_dir.glob("*.yaml")):
        try:
            d = yaml.load(f.read_text(), Loader=_Loader)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(d, dict):
            out.append((f.stem, d))
    return out


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


def ratios(digests_dir: Path, loaded: list | None = None) -> list[dict]:
    """cache read/write ratio per digest, newest field set only."""
    out = []
    for stem, d in loaded if loaded is not None else load_digests(digests_dir):
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
                "digest": stem,
                "read": read or 0,
                "write": write,
                "calls": calls or 0,
                "ratio": (read or 0) / write,
            }
        )
    return out


def collapsed(
    digests_dir: Path,
    threshold: float = COLLAPSE_RATIO,
    loaded: list | None = None,
) -> list[dict]:
    """Digests whose cacheable prefix looks broken rather than merely small."""
    return [
        r
        for r in ratios(digests_dir, loaded)
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
# The factor was 0.2 for as long as ONE median spanned populations that appeared
# to differ 4.3x, and it had to be: a single floor could not exceed 0.23 of the
# corpus median without condemning real video.
#
# Most of that apparent gap was the DIVISOR, not the sources. Dividing by
# materialised size instead of the raw file drops the document-vs-spoken gap from
# 4.30x to 1.35x, and the ceiling on a single floor rises from 0.23 to 0.57. The
# per-type split still earns its place - web runs 4.81 against video's 2.23 - but
# it is now correcting a real 2x difference rather than a measurement artefact.
#
# Held at 0.35 rather than tightened again. On the corrected basis the sparsest
# record of each type sits at 0.62-0.93 of its type median, so 0.35 leaves every
# real record 1.8-2.2x clear, and a factor near 0.6 would be the first false
# positive. The restraint is deliberate: SURVIVAL_FLOOR was tightened this
# morning on 181 records and had to be reverted within two hours when the
# population moved, and this corpus is 44 records with 97 more queued. Tighten
# when the medians have stopped moving, not before.
#
# Unlike SURVIVAL_FLOOR, this metric does NOT drift as review lands - that is the
# point of the materialised divisor. Review removes body from both the numerator's
# source and the denominator, so the ratio holds.
YIELD_FLOOR_FRACTION = 0.35
YIELD_MIN_KB = 20


def _records_by_hash(records_dir: Path) -> dict[str, Path]:
    """Record paths keyed by DECLARED content_hash.

    Not by filename: most records are symlinks into the content-addressed store
    so the resolved stem IS the hash, but not all are - two web records are
    regular files whose stem is the slug, and keying on path shape reported both
    as orphaned when both were present. Maps to the RECORD path rather than its
    resolved target, so a digest is still named from the record slug.
    """
    by_hash: dict[str, Path] = {}
    for rec in records_dir.glob("*.md"):
        t = rec.resolve()
        if not t.exists():
            continue
        by_hash.setdefault(t.stem.removesuffix(".v2"), rec)
        try:
            raw = rec.read_text(errors="replace")
        except OSError:
            continue
        if not raw.startswith("---"):
            continue
        try:
            fm = yaml.load(raw.split("---", 2)[1], Loader=_Loader)
        except (yaml.YAMLError, IndexError):
            continue
        if isinstance(fm, dict) and fm.get("content_hash"):
            by_hash[str(fm["content_hash"]).split(":")[-1]] = rec
    return by_hash


def materialised_size(record_md: Path, cache: dict | None = None) -> int | None:
    """Characters of the record that actually reach the model."""
    from anomalica_common.pre_digest import PREP_VERSION, materialise

    from digester.record_parser import parse_record

    try:
        raw = record_md.read_text(errors="replace")
    except OSError:
        return None
    key = f"matlen:{PREP_VERSION}:{hashlib.sha256(raw.encode()).hexdigest()}"
    if cache is not None and key in cache:
        return cache[key]
    try:
        n = len(materialise(parse_record(raw).body))
    except ValueError:
        return None
    if cache is not None:
        cache[key] = n
    return n


def claim_yields(
    digests_dir: Path,
    store_dir: Path,
    loaded: list | None = None,
    records_dir: Path | None = None,
) -> list[dict]:
    """claims-per-MATERIALISED-KB per digest, for records large enough to judge.

    The divisor is what the model SAW, not the file on disk. Claims come from the
    materialised pre-digest, so dividing by the raw record counts text that was
    never sent - and the error is exactly the fraction materialise removed.

    That fraction is not small and it is not uniform. Word timestamps are ~65-70%
    of a transcript's bytes, so a 900KB video record reaches the model as 260KB
    and its raw yield understates by 246%. Measured across the corpus, the
    document-vs-spoken gap is 4.30x on the raw divisor and 1.35x on this one:
    most of the "transcripts are less dense than documents" effect was the
    denominator, not the sources.

    Review is the second contributor and the one that grows. A reviewer marks
    irrelevant regions, materialise correctly drops them, and the record's
    measured yield falls by exactly that fraction - so a record that legitimately
    sheds half its body reads as half-yield, i.e. as a failed extraction. Today
    the corpus is ~99% unreviewed and this is nearly invisible; it arrives with
    the review programme. Dividing by materialised size makes the metric
    review-invariant, so no threshold above it needs recalibrating as review
    lands.
    """
    records_dir = records_dir or (store_dir.parent / "by-name")
    by_hash = _records_by_hash(records_dir) if records_dir.is_dir() else {}
    cache = _cache_load()
    out = []
    for stem, d in loaded if loaded is not None else load_digests(digests_dir):
        h = ((d.get("record") or {}).get("content_hash") or "").split(":")[-1]
        basis = "materialised"
        size = materialised_size(by_hash[h], cache) if h in by_hash else None
        if not size:
            # FALL BACK to the raw file, and say so. A record whose materialised
            # size cannot be computed still gets measured, but on a divisor known
            # to be wrong for transcripts - so the basis travels with the number
            # rather than being silently mixed in.
            basis = "raw"
            size = 0
            for c in (
                store_dir / f"{h}.v2.md",
                store_dir / f"{h}.md",
                store_dir / "v1" / f"{h}.v2.md",
                store_dir / "v1" / f"{h}.md",
            ):
                if c.exists():
                    size = c.stat().st_size
                    break
        kb = size / 1000
        if kb < YIELD_MIN_KB:
            continue
        n = len((d.get("domain_claims") or [])) + len(
            (d.get("infrastructure_claims") or [])
        )
        out.append(
            {
                "digest": stem,
                "kb": kb,
                "claims": n,
                "per_kb": n / kb,
                "basis": basis,
                "medium": (d.get("record") or {}).get("medium"),
            }
        )
    _cache_save(cache)
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


def low_yield(
    digests_dir: Path, store_dir: Path, loaded: list | None = None
) -> list[dict]:
    """Digests whose claim yield is so far below the norm FOR THEIR SOURCE TYPE
    that the extraction probably failed despite exiting successfully.

    Per type, because a single median over several populations is a weighted
    average and MIGRATES as the mix changes - and with ~97 records queued it
    migrates, taking the floor with it. A guard that gets less sensitive as more
    data arrives is the wrong shape.

    The populations differ far less than they first appeared. On the raw divisor
    documents ran ~2.85 claims/KB against spoken media's ~0.80, a 4.3x gap that
    looked like a property of the sources - transcripts carrying filler where
    documents are dense with assertions. Most of it was word timestamps inflating
    the denominator: on materialised size the gap is 1.35x. The split still
    matters (web 4.81, video 2.23) but for a real 2x difference, not a 4x one.

    A worked example of why the grouping is still needed: an ebook that lost 80%
    of its content is missed by a corpus-wide floor today and missed harder once
    the mix tilts, having changed not at all.
    """
    rows = claim_yields(digests_dir, store_dir, loaded)
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
# how much of the body survives materialise.
#
# UNLIKE the yield floor and the cache ratio, this one is genuinely uniform across
# media and a single threshold is correct. Measured over all 181 records:
#
#     audio  n= 15   0.822 is the lowest value ANYWHERE (web); every medium's
#     ebook  n= 17   min sits between 0.822 and 0.929, median 0.99-1.00, and
#     pdf    n= 26   the distributions overlap almost completely. There is no
#     video  n=113   3.5x split here of the kind that broke the other two.
#     web    n= 10
#
# (A ratio slightly above 1.0 is expected, not a fault: prep 6 renders span notes
# INTO the pre-digest, so it can carry more characters than the stripped baseline.)
#
# Tightened to 0.65 on that evidence and REVERTED to 0.5 within two hours, which
# is the more useful record of the two.
#
# The 181 records measured were almost entirely UNREVIEWED, and human review is
# precisely the process that legitimately removes body - a reviewer marks
# irrelevant regions and the pre-digest correctly drops them. Within two hours
# project-serpo was repointed from its pre-review copy to its reviewed one and
# landed at 0.676, against a floor of 0.65: 2.6% of margin on a record that is
# working exactly as intended. The reviewed population barely existed when the
# distribution was taken, and it is the population that will grow.
#
# That is the same error this module spent the day finding in other thresholds -
# a floor calibrated on one population while a different one arrives - committed
# here by the code that found it. Both other cases were caught by grouping the
# populations; that fix is not available yet at n=1 reviewed record, so the floor
# goes back to catching the disaster case only.
#
# The proper fix, once enough records carry review sidecars to have a norm: split
# reviewed from unreviewed and floor each on its own distribution, exactly as
# low_yield does by medium. Until then a loose floor that never lies beats a tight
# one that condemns good work.
SURVIVAL_FLOOR = 0.5


# Survival is a pure function of (record bytes, prep version), so it is cached
# rather than recomputed - a full corpus pass materialises every book and takes
# minutes of CPU. These are processor cycles, not model calls, so they cost no
# allowance; the cache exists so an independent timer can run this hourly without
# burning three minutes each time for an answer that cannot have changed.
#
# PREP_VERSION IS PART OF THE KEY, not decoration. materialise's behaviour is
# versioned, so keying on content alone would serve pre-change survival numbers
# after a prep change - a cached value whose inputs moved underneath it, which is
# the exact failure this module exists to detect. A prep bump invalidates every
# entry, which is correct: the answers genuinely changed.
_SURVIVAL_CACHE = Path.home() / ".cache" / "anomalica" / "pre-digest-survival.json"


def _cache_load() -> dict:
    try:
        return json.loads(_SURVIVAL_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _cache_save(cache: dict) -> None:
    try:
        _SURVIVAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SURVIVAL_CACHE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(_SURVIVAL_CACHE)
    except OSError:
        pass


def pre_digest_survival(record_md: Path, cache: dict | None = None) -> float | None:
    """Fraction of a record's body that survives into the pre-digest."""
    from anomalica_common.pre_digest import PREP_VERSION, materialise

    from digester.record_parser import parse_record

    from anomalica_common.pre_digest import strip_word_timestamps

    try:
        raw = record_md.resolve().read_text(errors="replace")
    except OSError:
        return None
    key = f"{PREP_VERSION}:{hashlib.sha256(raw.encode()).hexdigest()}"
    if cache is not None and key in cache:
        return cache[key]
    try:
        body = parse_record(raw).body
    except ValueError:
        return None
    if not body:
        return None
    # Measure against the body with WORD TIMESTAMPS already removed. A record/2
    # transcript is ~65% {{t:...}} tokens by character, and materialise strips
    # them correctly - so a raw ratio puts every transcript at ~30% and a flat
    # floor flags 129 records that are all fine. Comparing like with like isolates
    # what the ANNOTATIONS remove from what the transcript FORMAT removes.
    baseline = strip_word_timestamps(body) if body else None
    frac = len(materialise(body)) / len(baseline) if baseline else None
    if cache is not None:
        cache[key] = frac
    return frac


def over_marked(records_dir: Path, floor: float = SURVIVAL_FLOOR) -> list[dict]:
    """Records whose annotations remove so much body that a digest built from
    them would misrepresent the source."""
    cache = _cache_load()
    out = []
    for p in sorted(records_dir.glob("*.md")):
        frac = pre_digest_survival(p, cache)
        if frac is not None and frac < floor:
            out.append({"record": p.name, "survives": round(frac, 4)})
    _cache_save(cache)
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
        # 2026-08-28: REVERSED. Previously in UNWANTED on the reasoning that
        # copyright is access-control state whose authority is the ingest record,
        # so a copy elsewhere is a staler second source of truth. Sound, and
        # wrong: with the graph blind to copyright the assimilator nearly
        # published verbatim excerpts from 13 copyrighted books. The digest now
        # carries the STATUS only, flattened to copyright_status.
        "copyright",
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


# --- PRE-DIGEST FRESHNESS ---
#
# A digest records the sha256 of the exact pre-digest text the model was given
# (ADR 0042), so the input is reproducible. Nothing compared it back.
#
# Two ways a digest silently stops matching its source. The record is EDITED after
# digestion - a reviewer fixes a transcript, an ingest is re-run - and the digest
# still describes the old text while pointing at the new. Or PREP_VERSION moves,
# which changes what materialise produces, so every claim `location` is an offset
# into a frame that no longer exists: spans resolve to the wrong text rather than
# failing to resolve, which is the worse outcome because it looks like it worked.
#
# Neither shows up in the digest, the record, or an exit code. The corpus is
# currently 78 digests at prep 6 with 2 predating the block, so version drift is
# clean today - and it stays clean only if something checks.


def stale_record_paths(
    digests_dir: Path, records_dir: Path, loaded: list | None = None
) -> list[Path]:
    """Record files whose digest was built from text the record no longer has.

    DERIVED from the artefacts rather than remembered in a list. The queue's
    normal skip is "has a digest" and FORCE's is "has a provenance_chain";
    neither is the right test for a record whose SOURCE moved underneath a
    perfectly complete digest, so re-digestion work kept having to be tracked by
    hand - and three separate hand-built lists this week is the signal that the
    fact was recoverable from the data all along.
    """
    out = []
    for f in pre_digest_freshness(digests_dir, records_dir, loaded):
        if f["issue"] in ("record_changed", "prep_version") and f.get("record"):
            out.append(f["record"])
    return out


def pre_digest_freshness(
    digests_dir: Path, records_dir: Path, loaded: list | None = None
) -> list[dict]:
    """Digests whose recorded pre-digest no longer matches the record.

    The recomputed hash is compared BEFORE the prep version is consulted. A
    PREP_VERSION bump changes materialise's output only for records carrying
    the markers it touched, so a digest whose text hashes identically under
    the current prep is fresh whatever version built it. Reporting on the
    version first flagged 86 of 108 digests after the 6 -> 7 bump and buried
    the 32 whose text had actually moved.

    The body is read from the content-addressed store when the digest's hash
    resolves there. `by-name/` is documented as symlinks into the store, but
    24 entries are regular files holding the body as first ingested, and two
    of those records were reviewed and rewritten in the store afterwards -
    compared against the by-name copy, both read as fresh.
    """
    from anomalica_common.pre_digest import PREP_VERSION, materialise, pre_digest_hash

    from digester.record_parser import parse_record

    by_hash = _records_by_hash(records_dir)
    store = records_dir.parent / "store"

    cache = _cache_load()
    out = []
    for stem, d in loaded if loaded is not None else load_digests(digests_dir):
        pd = d.get("pre_digest") or {}
        recorded, version = pd.get("sha256"), pd.get("prep_version")
        if not recorded:
            continue
        h = ((d.get("record") or {}).get("content_hash") or "").split(":")[-1]
        rec = by_hash.get(h)
        if rec is None:
            # ORPHANED: the digest names a source that is no longer in the record
            # tree, so its freshness can never be evaluated. Reported rather than
            # skipped - a check that silently declines to examine something is
            # indistinguishable from one that examined it and found nothing.
            out.append(
                {
                    "digest": stem,
                    "issue": "orphaned",
                    "detail": f"no record for content_hash {h[:12] or '(absent)'}",
                }
            )
            continue
        body_path = next(
            (p for p in (store / f"{h}.md", store / f"{h}.v2.md") if p.is_file()),
            rec,
        )
        try:
            raw = body_path.read_text(errors="replace")
        except OSError:
            continue
        key = f"pdsha:{PREP_VERSION}:{hashlib.sha256(raw.encode()).hexdigest()}"
        actual = cache.get(key)
        if actual is None:
            try:
                actual = pre_digest_hash(materialise(parse_record(raw).body))
            except ValueError:
                continue
            cache[key] = actual
        if actual == recorded:
            continue
        if version is not None and version != PREP_VERSION:
            out.append(
                {
                    "digest": stem,
                    "issue": "prep_version",
                    "detail": (
                        f"built under prep {version}, current is {PREP_VERSION}; "
                        f"recorded {recorded[:12]}, record now yields {actual[:12]}"
                    ),
                    "record": rec,
                }
            )
        else:
            out.append(
                {
                    "digest": stem,
                    "issue": "record_changed",
                    "detail": f"recorded {recorded[:12]}, record now yields {actual[:12]}",
                    "record": rec,
                }
            )
    _cache_save(cache)
    return out
