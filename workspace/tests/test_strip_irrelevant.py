"""Pre-extraction strip of reviewer-marked irrelevant content."""

from digester.extract import strip_irrelevant


def test_irrelevant_speaker_segment_dropped_relevant_kept():
    body = (
        "<!-- speaker: Bill Whitaker -->\n"
        "00:00:01.8 The Pentagon acknowledged UAP.\n"
        "<!-- speaker: [irrelevant] -->\n"
        "00:00:10.0 And now a word from our sponsors.\n"
        "00:00:14.0 Buy soap.\n"
        "<!-- speaker: Luis Elizondo -->\n"
        "00:00:20.0 The government has stated they are real.\n"
    )
    out = strip_irrelevant(body)
    assert "Buy soap" not in out
    assert "word from our sponsors" not in out
    assert "[irrelevant]" not in out
    assert "The Pentagon acknowledged UAP." in out
    assert "The government has stated they are real." in out
    # relevant speaker comments survive for attribution
    assert "<!-- speaker: Bill Whitaker -->" in out
    assert "<!-- speaker: Luis Elizondo -->" in out


def test_irrelevant_speaker_at_end_drops_to_eof():
    body = (
        "<!-- speaker: Host -->\n"
        "00:00:01.0 Real content.\n"
        "<!-- speaker: [irrelevant] -->\n"
        "00:00:05.0 Outro chatter.\n"
        "00:00:07.0 See you next week.\n"
    )
    out = strip_irrelevant(body)
    assert "Real content." in out
    assert "Outro chatter." not in out
    assert "See you next week." not in out


def test_prose_region_dropped_markers_and_between():
    body = (
        "The relevant opening paragraph.\n"
        "<!-- irrelevant:start -->\n"
        "Acknowledgements: thanks to my editor and my cat.\n"
        "Copyright boilerplate.\n"
        "<!-- irrelevant:end -->\n"
        "The relevant closing paragraph.\n"
    )
    out = strip_irrelevant(body)
    assert "relevant opening paragraph" in out
    assert "relevant closing paragraph" in out
    assert "Acknowledgements" not in out
    assert "Copyright boilerplate" not in out
    assert "irrelevant:start" not in out and "irrelevant:end" not in out


def test_combined_transcript_and_prose():
    body = (
        "<!-- irrelevant:start -->\nfront matter\n<!-- irrelevant:end -->\n"
        "<!-- speaker: A -->\nkeep this\n"
        "<!-- speaker: [irrelevant] -->\ndrop this\n"
        "<!-- speaker: B -->\nkeep that\n"
    )
    out = strip_irrelevant(body)
    assert "keep this" in out and "keep that" in out
    assert "front matter" not in out and "drop this" not in out


def test_canonical_spaced_marker_form():
    # record-format.md canonical: space after the colon (valid YAML key: value)
    body = (
        "keep before\n"
        "<!-- irrelevant: start -->\n"
        "drop me\n"
        "<!-- irrelevant: end -->\n"
        "keep after\n"
    )
    out = strip_irrelevant(body)
    assert "keep before" in out and "keep after" in out
    assert "drop me" not in out and "irrelevant:" not in out


def test_no_markers_is_identity():
    body = "<!-- speaker: A -->\n00:00:01 hello\nplain text\n"
    assert strip_irrelevant(body) == body
