"""The yield floor's sensitivity must not depend on the corpus mix.

`low_yield` exists because hair-of-the-alien reached the model as 1,023 characters
of an 88,384-character body and wrote a digest that read like a completed book,
exiting 0. Nothing else distinguishes that from a good extraction.

The failure mode being tested for is not a wrong answer - it is the guard becoming
LESS sensitive as more data arrives, which no test of a fixed corpus would show.
"""

from __future__ import annotations

import yaml

from digester.health import low_yield


def _corpus(tmp_path, records):
    """records: (name, medium, kb, claims). Returns (digests_dir, store_dir)."""
    d, s = tmp_path / "digests", tmp_path / "store"
    d.mkdir(exist_ok=True)
    s.mkdir(exist_ok=True)
    for i, (name, medium, kb, claims) in enumerate(records):
        h = f"{i:064x}"
        (s / f"{h}.md").write_text("x" * int(kb * 1000))
        (d / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "record": {"content_hash": f"sha256:{h}", "medium": medium},
                    "domain_claims": [{"c": n} for n in range(claims)],
                }
            )
        )
    return d, s


def _flagged(tmp_path, records):
    return {r["digest"] for r in low_yield(*_corpus(tmp_path, records))}


def test_a_gutted_ebook_is_caught_against_its_own_type(tmp_path):
    # 13 healthy ebooks at ~2.8 claims/KB, one that lost 80% of its content.
    books = [(f"book{i}", "ebook", 100, 280) for i in range(13)]
    # 0.42 cl/KB - 15% of the type median. Note how far a book must fall before
    # the 0.2 factor reacts at all: at exactly 20% it passes, so a book losing
    # four fifths of its content is inside tolerance by construction.
    books.append(("gutted", "ebook", 100, 42))
    assert "gutted" in _flagged(tmp_path, books)


def test_the_floor_does_not_migrate_when_video_floods_the_corpus(tmp_path):
    # THE REGRESSION THIS FILE EXISTS FOR. A corpus-wide median is a weighted
    # average of two populations that differ 3.5x, so it migrates as the mix
    # changes - and the queue holds far more video than documents. Under the old
    # single-median rule this ebook is caught in a document-heavy corpus and
    # missed once video lands, having changed not at all.
    books = [(f"book{i}", "ebook", 100, 280) for i in range(13)]
    books.append(("gutted", "ebook", 100, 42))
    assert "gutted" in _flagged(tmp_path, books)

    flooded = books + [(f"vid{i}", "video", 100, 80) for i in range(108)]
    assert "gutted" in _flagged(tmp_path, flooded), (
        "the floor followed the corpus mix instead of the ebook population"
    )


def test_a_normal_transcript_is_not_condemned_by_document_density(tmp_path):
    # The reverse error: spoken media legitimately runs ~0.80 claims/KB against
    # ~2.85 for documents. Measured against documents, every transcript looks
    # like a failed extraction.
    corpus = [(f"book{i}", "ebook", 100, 280) for i in range(13)]
    corpus += [(f"vid{i}", "video", 100, 80) for i in range(6)]
    assert not _flagged(tmp_path, corpus)


def test_a_thin_type_inherits_its_FAMILY_not_the_corpus(tmp_path):
    # audio sits at n=2, below the per-type minimum. Falling back to the corpus
    # would judge a transcript against a document-weighted floor - reintroducing
    # the averaging the split removes, exactly for the types too thin to notice.
    corpus = [(f"book{i}", "ebook", 100, 300) for i in range(13)]
    corpus += [(f"vid{i}", "video", 100, 70) for i in range(17)]
    corpus += [("audio1", "audio", 100, 65), ("audio2", "audio", 100, 65)]
    assert not _flagged(tmp_path, corpus), "audio judged against document density"


def test_a_thin_document_type_is_not_slackened_by_the_spoken_population(tmp_path):
    # The same fallback in the other direction: web at n=2 must inherit the
    # document family, not a corpus number dragged down by video. A web record at
    # spoken density is under-extracted and must be caught.
    corpus = [(f"book{i}", "ebook", 100, 300) for i in range(13)]
    corpus += [(f"vid{i}", "video", 100, 70) for i in range(17)]
    corpus += [("web1", "web", 100, 430), ("gutted_web", "web", 100, 40)]
    assert "gutted_web" in _flagged(tmp_path, corpus)


def test_too_few_records_to_have_a_norm_flags_nothing(tmp_path):
    assert not _flagged(tmp_path, [("a", "ebook", 100, 1), ("b", "ebook", 100, 300)])


def test_short_records_are_not_judged_by_a_book_calibrated_ratio(tmp_path):
    # The cache ratio is structurally bounded by call count: each pass writes its
    # prefix on the first call and reads nothing back, so few calls means the write
    # is amortised over almost nothing. COLLAPSE_RATIO was measured on books at
    # 34-120 calls; a 3-call video legitimately sits near it.
    from digester.health import collapsed

    d = tmp_path / "d"
    d.mkdir()
    for name, read, write, calls in (
        ("short_video", 300, 1000, 3),  # 0.30 - normal for its call count
        ("real_collapse", 30, 1000, 40),  # 0.03 over enough calls to mean it
        ("healthy_book", 1000, 1000, 40),
    ):
        (d / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "ai_usage": [
                        {
                            "tokens": {
                                "cache_read": read,
                                "cache_write": write,
                                "calls": calls,
                            }
                        }
                    ]
                }
            )
        )
    assert {r["digest"] for r in collapsed(d)} == {"real_collapse"}


def test_yield_is_measured_against_what_the_model_SAW(tmp_path, monkeypatch):
    # The divisor is materialised size, not the file on disk. A transcript is
    # ~65-70% word-timestamp tokens by byte, so the raw file overstates the
    # denominator and understates yield - a 900KB video record reaches the model
    # as 260KB. Review does the same thing and grows: a reviewer marks irrelevant
    # regions, materialise drops them, and the record's measured yield falls by
    # exactly that fraction, so legitimately-reviewed work reads as failed
    # extraction. Dividing by what was sent makes the metric review-invariant.
    from digester import health

    d, s = tmp_path / "d", tmp_path / "s"
    r = tmp_path / "records"
    for p in (d, s, r):
        p.mkdir()
    h = f"{1:064x}"
    (s / f"{h}.md").write_text("x" * 400_000)  # raw file: 400KB
    (r / "rec.md").write_text(f"---\ncontent_hash: sha256:{h}\n---\nbody\n")
    (d / "rec.yaml").write_text(
        yaml.safe_dump(
            {
                "record": {"content_hash": f"sha256:{h}", "medium": "video"},
                "domain_claims": [{"c": n} for n in range(200)],
            }
        )
    )
    # only 100KB of that 400KB survives to the model
    monkeypatch.setattr(health, "materialised_size", lambda p, cache=None: 100_000)
    row = health.claim_yields(d, s, records_dir=r)[0]
    assert row["basis"] == "materialised"
    assert row["per_kb"] == 2.0, "measured against the raw 400KB, not the sent 100KB"
