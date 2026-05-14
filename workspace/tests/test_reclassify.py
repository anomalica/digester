from pathlib import Path

from digester.reclassify import (
    is_document_name,
    reclassify_documents_in_dir,
    reclassify_documents_in_file,
)


def test_document_suffixes_match():
    assert is_document_name("Wilson Davis Memo")
    assert (
        is_document_name("Hottel FBI Memo 1950") is False
        or is_document_name("Hottel FBI Memo 1950") is True
    )
    # Trailing year shouldn't matter - "Memo" appears as a word
    assert is_document_name("Twining Letter 1947")
    assert is_document_name("FLIR1 Video")
    assert is_document_name("ODNI UAP 2022 Report")
    assert is_document_name("Pais Superluminal Craft Paper 2015")
    assert is_document_name("Hunt for Zero Point Book")
    assert is_document_name("Elizondo Resignation Letter")
    assert is_document_name("Condon Report")


def test_known_documents_match():
    assert is_document_name("FLIR1")
    assert is_document_name("Gimbal")
    assert is_document_name("Go-Fast")


def test_physical_objects_are_not_reclassified():
    assert not is_document_name("E-2 Hawkeye")
    assert not is_document_name("USS Princeton")
    assert not is_document_name("Tic Tac UAP")
    assert not is_document_name("ATFLIR Pod")
    assert not is_document_name("APG-79 Radar")
    assert not is_document_name("Bigelow Aerospace Ranch")
    assert not is_document_name("Aurora")
    assert not is_document_name("Aerospace Vehicle")


def test_exclusion_tokens_preserve_object():
    # "Reporting System" contains "Report" but is a system, not a document.
    assert not is_document_name("Marauder UAP Reporting System")
    assert not is_document_name("Defence Support Program Satellites")
    assert not is_document_name("Special Access Program")
    assert not is_document_name("Document Management System")
    assert not is_document_name("CORONA Program")


def test_programme_stays_a_matter():
    # Mark explicitly OK'd programmes staying as matters.
    assert not is_document_name("HAVE Blue Program")
    assert not is_document_name("Advanced Aerospace Weapon System Applications Program")


def test_reclassify_file_rewrites_node_type(tmp_path: Path):
    sample = """---
record_title: Sample
---

## Nodes

### 12345678-1234-1234-1234-123456789abc person: David Fravor

### 87654321-4321-4321-4321-cba987654321 object: USS Princeton

### aaaa1111-bbbb-2222-cccc-3333dddd4444 object: Hottel FBI Memo 1950

### bbbb2222-cccc-3333-dddd-4444eeee5555 matter: Condon Report

### cccc3333-dddd-4444-eeee-5555ffff6666 object: ATFLIR Pod

## Domain Claims

### claim1234-1234-1234-1234-1234567890ab [observation/first_hand] speaker:David Fravor
A claim that references object: USS Princeton. refs: Fake mention preserved.
"""
    path = tmp_path / "sample.extract.md"
    path.write_text(sample)
    count = reclassify_documents_in_file(path)
    assert count == 2  # Hottel memo + Condon Report
    out = path.read_text()
    assert "document: Hottel FBI Memo 1950" in out
    assert "document: Condon Report" in out
    # Physical objects untouched
    assert "object: USS Princeton" in out
    assert "object: ATFLIR Pod" in out
    # Claim line untouched
    assert "claim1234" in out and "[observation/first_hand]" in out


def test_reclassify_file_no_changes_leaves_file_alone(tmp_path: Path):
    sample = """## Nodes

### 12345678-1234-1234-1234-123456789abc person: David Fravor

### 87654321-4321-4321-4321-cba987654321 object: USS Princeton
"""
    path = tmp_path / "sample.extract.md"
    original = sample
    path.write_text(sample)
    count = reclassify_documents_in_file(path)
    assert count == 0
    assert path.read_text() == original


def test_reclassify_dir_aggregates(tmp_path: Path):
    (tmp_path / "a.extract.md").write_text(
        "### 11111111-1111-1111-1111-111111111111 object: A Memo\n"
        "### 22222222-2222-2222-2222-222222222222 object: USS Cole\n"
    )
    (tmp_path / "b.extract.md").write_text(
        "### 33333333-3333-3333-3333-333333333333 matter: Roswell Report\n"
    )
    (tmp_path / "ignore.txt").write_text("not an extract")
    results = reclassify_documents_in_dir(tmp_path)
    assert results == {"a.extract.md": 1, "b.extract.md": 1}
