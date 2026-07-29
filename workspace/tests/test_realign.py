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
