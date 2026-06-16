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
