from digester.record_parser import parse_record


def test_parse_frontmatter():
    text = """---
schema: anomalica/record/1
title: Test Document
date: 2004-11-14
creators:
  - Alice
  - Bob
source_type: interview
---

Body text here.
"""
    result = parse_record(text)
    assert result.title == "Test Document"
    assert result.date == "2004-11-14"
    assert result.creators == ["Alice", "Bob"]
    assert result.source_type == "interview"
    assert result.schema_version == "anomalica/record/1"
    assert "Body text here." in result.body


def test_parse_canonical_date_published_and_source_url():
    # Real records (record/1 and /2) use date_published + source_url, not
    # date/reference. Without this the digest dropped date and the source link.
    text = """---
schema: anomalica/record/2
title: A Video
date_published: 2023-08-11
source_type: video
publisher: Area52
source_url: https://www.youtube.com/watch?v=abc123
---

Body.
"""
    result = parse_record(text)
    assert result.date == "2023-08-11"
    assert result.reference == "https://www.youtube.com/watch?v=abc123"
    assert result.metadata.get("publisher") == "Area52"


def test_midnight_iso_date_collapses_to_date():
    # YouTube publishes a date as a midnight-UTC timestamp; the time is noise.
    text = """---
title: A Video
date_published: 2026-04-24T00:00:00.000Z
source_type: video
---

Body.
"""
    assert parse_record(text).date == "2026-04-24"


def test_partial_date_precision_passes_through():
    text = "---\ntitle: T\ndate_published: 2023-07\n---\n\nBody.\n"
    assert parse_record(text).date == "2023-07"


def test_date_legacy_fallback_and_no_creators():
    # legacy `date` still works; a video with only a publisher has no creators
    text = """---
title: Legacy
date: 2004-11-14
publisher: CBS News
---

Body.
"""
    result = parse_record(text)
    assert result.date == "2004-11-14"
    assert result.creators == []  # producer will be None - correct per spec


def test_parse_page_annotations():
    text = """---
title: Multi-page
---

---
file_page: 1
---

Page one content.

---
file_page: 2
---

Page two content.
"""
    result = parse_record(text)
    assert len(result.pages) == 2
    assert result.pages[0].file_page == 1
    assert result.pages[1].file_page == 2
    assert "Page one content." in result.body
    assert "Page two content." in result.body


def test_parse_empty():
    result = parse_record("")
    assert result.title == ""
    assert result.body == ""
    assert result.pages == []


def test_parse_no_frontmatter():
    text = "Just some text without frontmatter."
    result = parse_record(text)
    assert result.title == ""
    assert "Just some text" in result.body


def test_parse_record_rejects_a_path_instead_of_contents():
    # A path is a str, so the signature cannot catch this. Unguarded, the parser
    # treats the filename as a record body and returns a plausible 108-character
    # result for a 778KB book - no exception, every downstream number wrong.
    import pytest

    with pytest.raises(ValueError, match="CONTENTS, not a path"):
        parse_record("/home/mark/repos/anomalica/ingests/records/some-record.md")


def test_parse_record_still_accepts_a_short_real_record():
    r = parse_record("---\ntitle: Short\n---\nA body.\n")
    assert r.title == "Short" and "A body." in r.body
