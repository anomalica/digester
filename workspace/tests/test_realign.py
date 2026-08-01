from digester.realign import (
    align_quote,
    words_from_record1,
    words_from_record2,
    words_from_sidecar,
)


def test_aligns_verbatim_quote_to_start_and_end():
    words = ["the", "object", "descended", "rapidly", "over", "the", "water"]
    times = [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 3.0]
    r = align_quote("the object descended rapidly", words, times, "word")
    assert r is not None
    assert r.start == 1.0
    assert r.end == 2.0
    assert r.coverage == 1.0
    assert r.resolution == "word"
    assert not r.ambiguous


def test_duplicate_phrase_picks_occurrence_with_matching_neighbours():
    # "the object moved" appears twice; only the second continues with the full
    # quote. Windowed full-quote scoring must prefer the second occurrence.
    words = [
        "the",
        "object",
        "moved",
        "fast",
        "away",
        "then",
        "the",
        "object",
        "moved",
        "slowly",
        "and",
        "vanished",
    ]
    times = [10.0] * 5 + [20.0] + [30.0] * 6
    r = align_quote("the object moved slowly and vanished", words, times, "word")
    assert r is not None
    assert r.start == 30.0  # the second occurrence, not the first at 10.0
    assert r.coverage == 1.0


def test_genuinely_ambiguous_quote_is_flagged():
    # The same full phrase appears twice, far apart in time -> ambiguous.
    phrase = ["it", "was", "aware", "of", "us"]
    words = phrase + ["pause"] * 3 + phrase
    times = [10.0] * 5 + [50.0] * 3 + [90.0] * 5
    r = align_quote("it was aware of us", words, times, "word")
    assert r is not None
    assert r.ambiguous


def test_unpresent_quote_returns_none():
    words = ["completely", "different", "transcript", "content", "here"]
    times = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert (
        align_quote("nothing like this appears at all anywhere", words, times, "word")
        is None
    )


def test_empty_inputs_return_none():
    assert align_quote("", ["a"], [1.0], "word") is None
    assert align_quote("a", [], [], "word") is None


def test_record1_adapter_reads_sentence_prefixes():
    body = (
        "# Title\n"
        "<!-- speaker: A -->\n"
        "00:00:01.0 We saw the object\n"
        "00:00:05.5 It moved away\n"
    )
    words, times = words_from_record1(body)
    assert words == ["we", "saw", "the", "object", "it", "moved", "away"]
    assert times[0] == 1.0
    assert times[-1] == 5.5


def test_record2_adapter_reads_inline_tokens():
    body = "<!-- speaker: A -->\n00:00:01.0 {{t:1.20}}We {{t:1.40}}saw {{t:1.60}}it\n"
    words, times = words_from_record2(body)
    assert words == ["we", "saw", "it"]
    assert times == [1.2, 1.4, 1.6]


def test_sidecar_adapter_flattens_segments_in_order():
    sidecar = {
        "schema": "anomalica/words/1",
        "segments": [
            {
                "start": 1.0,
                "words": [{"w": "We", "start": 1.2}, {"w": "saw", "start": 1.4}],
            },
            {"start": 2.0, "words": [{"w": "it", "start": 2.1}]},
        ],
    }
    words, times = words_from_sidecar(sidecar)
    assert words == ["we", "saw", "it"]
    assert times == [1.2, 1.4, 2.1]


def test_word_level_alignment_through_record2_adapter():
    body = (
        "00:01:08.3 {{t:69.27}}I {{t:69.37}}know {{t:69.67}}Susan "
        "{{t:69.97}}what {{t:70.11}}you {{t:70.31}}asked {{t:70.58}}for\n"
    )
    words, times = words_from_record2(body)
    r = align_quote("I know, Susan, what you asked for", words, times, "word")
    assert r is not None
    assert r.start == 69.27
    assert r.end == 70.58
    assert r.coverage == 1.0


def test_speaker_prefix_is_stripped_before_alignment():
    """A model that prepends "Name: " to an otherwise verbatim quote must still
    anchor. The label is never in the word stream - the record carries the
    speaker as a separate annotation - so unstripped it cannot match, the quote
    falls through to the unbounded global match, and the span runs from the
    label's first occurrence anywhere in the record to the real content."""
    from digester.realign import align_quote

    words = "he gave me for five hundred dollars the first name".split()
    times = [float(i) for i in range(len(words))]
    plain = align_quote("he gave me for five hundred", words, times, "timecode")
    prefixed = align_quote(
        "Jon Stewart: he gave me for five hundred", words, times, "timecode"
    )
    assert plain is not None and prefixed is not None
    assert (prefixed.start, prefixed.end) == (plain.start, plain.end)


def test_global_fallback_refuses_a_disproportionate_span():
    """The windowed path is bounded to the quote's length, so only the global
    fallback can join a stray token to content far away. A quote that can only be
    matched across a huge stretch is not verbatim-present; refusing leaves the
    claim with whatever the model wrote, which is honest, rather than a confident
    span no reviewer can check."""
    from digester.realign import align_quote

    # "alpha" at the start, "omega" 400 words later, nothing in between matches.
    words = ["alpha"] + ["filler"] * 400 + ["omega"]
    times = [float(i) for i in range(len(words))]
    assert align_quote("alpha omega", words, times, "timecode") is None


def test_short_document_global_fallback_still_aligns():
    """The guard must not reject ordinary small documents, where a span a few
    times the quote length is normal rather than degenerate."""
    from digester.realign import align_quote

    words = "the object was observed by two aircrew over the pacific".split()
    times = [float(i) for i in range(len(words))]
    assert align_quote("object observed aircrew", words, times, "char") is not None


def test_chapter_spans_and_relative_locations():
    """A global character offset only means anything against one exact
    pre-digest: it moves when the handler re-extracts and again on any
    PREP_VERSION bump. A chapter label comes from the source's own structure and
    survives both, so only the within-chapter offset has to be recomputed."""
    from digester.realign import chapter_spans, offsets_to_span

    body = (
        "<!-- chapter: 1 -->\nfirst chapter text here\n"
        "<!-- chapter: 2 -->\nsecond chapter text here\n"
    )
    chs = chapter_spans(body)
    assert [c[0] for c in chs] == ["1", "2"]

    ch2_start = chs[1][1]
    assert offsets_to_span(ch2_start + 5, ch2_start + 12, chs) == "ch2:5-12"
    assert offsets_to_span(chs[0][1] + 2, chs[0][1] + 9, chs) == "ch1:2-9"


def test_offsets_to_span_falls_back_to_char_without_chapters():
    """Every record type but ebooks has no chapter markers; those keep the bare
    pre-digest span rather than acquiring a fabricated chapter."""
    from digester.realign import chapter_spans, offsets_to_span

    assert chapter_spans("no markers at all") == []
    assert offsets_to_span(10, 20, []) == "char:10-20"
    assert offsets_to_span(10, 20, None) == "char:10-20"


def test_untimed_locations_are_chapter_relative_when_chapters_exist():
    from digester.realign import normalise_untimed_locations

    body = (
        "<!-- chapter: 1 -->\nthe committee met in October to review the matter\n"
        "<!-- chapter: 2 -->\nthe object was observed by two aircrew over the pacific\n"
    )
    claims = [{"location": "?", "quote": "observed by two aircrew"}]
    normalise_untimed_locations(claims, body)
    assert claims[0]["location"].startswith("ch2:")


def test_cache_collapse_canary_flags_a_broken_prefix_not_a_weak_one(tmp_path):
    """Prompt caching only pays when the stable part of a call precedes the
    variable part. If that order is ever reversed, every call rewrites what it
    used to read - the extraction stays correct, every test passes, and only the
    cost characteristic moves. The read/write ratio is the one signal that does."""
    import yaml
    from digester.health import collapsed, ratios

    def write(name, read, write_, calls):
        (tmp_path / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "ai_usage": [
                        {
                            "stage": "digest",
                            "tokens": {
                                "cache_read": read,
                                "cache_write": write_,
                                "calls": calls,
                            },
                        }
                    ]
                }
            )
        )

    write("healthy", 9_245_292, 9_170_506, 100)  # Imminent, measured
    write("weakest_real", 815_483, 1_011_281, 40)  # Hair of the Alien, measured
    write("collapsed", 12_000, 4_000_000, 40)  # prefix lost
    write("single_call", 0, 188_848, 1)  # nothing to read back, legitimately

    assert len(ratios(tmp_path)) == 4
    flagged = {r["digest"] for r in collapsed(tmp_path)}
    assert flagged == {"collapsed"}, flagged
