"""Parse Anomalica record interchange format files.

Records are markdown files with YAML frontmatter and inline YAML annotation
blocks. See ADR 0012 for the full specification.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

import yaml

# A date-only publication date that arrived as a midnight ISO timestamp (e.g.
# YouTube's "2026-04-24T00:00:00.000Z"); the time carries no information.
_MIDNIGHT_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00(?:\.0+)?(?:Z|\+00:00)?$")


def _normalise_date(value) -> str | None:
    """Coerce a frontmatter date into a clean display string. Midnight (no real
    time-of-day) collapses to YYYY-MM-DD; a meaningful time is kept; partial
    precision (`2023`, `2023-07`) passes through unchanged."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    s = str(value).strip()
    m = _MIDNIGHT_ISO.match(s)
    return m.group(1) if m else s


@dataclass
class ParsedRecord:
    """A parsed record file ready for digestion."""

    title: str = ""
    date: str | None = None
    creators: list[str] = field(default_factory=list)
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
                    # Canonical frontmatter is `date_published` (record-format
                    # spec); `date` is a legacy fallback. Without this every
                    # digest carried a null date.
                    record.date = _normalise_date(
                        fm.get("date_published") or fm.get("date")
                    )
                    record.creators = fm.get("creators", [])
                    record.source_type = fm.get("source_type")
                    # The spec has no `reference` field; the source link lives in
                    # `source_url`. Map it so the digest carries provenance.
                    record.reference = fm.get("reference") or fm.get("source_url")
                    record.schema_version = fm.get("schema")
                    record.metadata = {
                        k: v
                        for k, v in fm.items()
                        if k
                        not in (
                            "title",
                            "date",
                            "date_published",
                            "creators",
                            "source_type",
                            "reference",
                            "source_url",
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
            if end is not None and _is_annotation_block(body_lines[i + 1 : end]):
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


# Annotation keys that may appear in a fenced block in the body. A fence is only
# an annotation if what follows actually looks like one.
_ANNOTATION_KEYS = ("file_page", "printed_page", "chapter", "speaker", "image")


def _is_annotation_block(lines: list[str]) -> bool:
    """Whether a fenced region is an annotation rather than ordinary prose.

    A bare `---` is legal prose - a section break, a horizontal rule - and books
    use it. Treating every fence as an annotation opener made the parser swallow
    everything up to the next one: Hair of the Alien carries 12 fences, and its
    620KB body was silently reduced to 88KB with no error, no warning, and a
    digest that looked complete. Nine claims from a book, and three sessions
    hunting the loss in the wrong places.

    So the fence must be CORROBORATED by its content before anything is dropped.
    Prose that happens to sit between two horizontal rules is prose.
    """
    text = "\n".join(lines).strip()
    if not text or len(lines) > 40:
        return False
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    return isinstance(parsed, dict) and any(k in parsed for k in _ANNOTATION_KEYS)


def _find_fence_end(lines: list[str], start: int) -> int | None:
    """Find the closing --- fence starting from the given line index."""
    for i in range(start, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None
