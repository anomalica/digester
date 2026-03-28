"""Parse Anomalica record interchange format files.

Records are markdown files with YAML frontmatter and inline YAML annotation
blocks. See ADR 0012 for the full specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass
class ParsedRecord:
    """A parsed record file ready for digestion."""

    title: str = ""
    date: str | None = None
    authors: list[str] = field(default_factory=list)
    source_type: str | None = None
    reference: str | None = None
    schema_version: str | None = None
    metadata: dict = field(default_factory=dict)
    body: str = ""
    pages: list[PageBreak] = field(default_factory=list)


@dataclass
class PageBreak:
    """A page boundary annotation within a record."""

    file_page: int
    offset: int


def parse_record(text: str) -> ParsedRecord:
    """Parse a record interchange format file into structured data."""
    lines = text.split("\n")
    record = ParsedRecord()

    if not lines:
        return record

    # Parse YAML frontmatter
    if lines[0].strip() == "---":
        end = _find_fence_end(lines, 1)
        if end is not None:
            frontmatter_text = "\n".join(lines[1:end])
            try:
                fm = yaml.safe_load(frontmatter_text)
                if isinstance(fm, dict):
                    record.title = fm.get("title", "")
                    record.date = fm.get("date")
                    if isinstance(record.date, str):
                        pass
                    elif record.date is not None:
                        record.date = str(record.date)
                    record.authors = fm.get("authors", [])
                    record.source_type = fm.get("source_type")
                    record.reference = fm.get("reference")
                    record.schema_version = fm.get("schema")
                    record.metadata = {
                        k: v
                        for k, v in fm.items()
                        if k
                        not in (
                            "title",
                            "date",
                            "authors",
                            "source_type",
                            "reference",
                            "schema",
                        )
                    }
            except yaml.YAMLError:
                pass
            body_lines = lines[end + 1 :]
        else:
            body_lines = lines
    else:
        body_lines = lines

    # Parse body, extracting annotation blocks
    content_parts = []
    i = 0
    while i < len(body_lines):
        line = body_lines[i]
        if line.strip() == "---":
            end = _find_fence_end(body_lines, i + 1)
            if end is not None:
                annotation_text = "\n".join(body_lines[i + 1 : end])
                try:
                    annotation = yaml.safe_load(annotation_text)
                    if isinstance(annotation, dict) and "file_page" in annotation:
                        record.pages.append(
                            PageBreak(
                                file_page=int(annotation["file_page"]),
                                offset=len("\n".join(content_parts)),
                            )
                        )
                except yaml.YAMLError:
                    pass
                i = end + 1
                continue
        content_parts.append(line)
        i += 1

    record.body = "\n".join(content_parts).strip()
    return record


def _find_fence_end(lines: list[str], start: int) -> int | None:
    """Find the closing --- fence starting from the given line index."""
    for i in range(start, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None
