"""One-off taxonomy fixes applied to existing .extract.md files.

The .extract.md files are the source of truth, so reclassifying nodes means
rewriting node_type values in those files. After running these passes the
database is rebuilt to pick up the changes.
"""

from __future__ import annotations

import re
from pathlib import Path

# Names ending in or containing one of these tokens are documents. Word-boundary
# match so "Marauder UAP Reporting System" doesn't trip "Report".
_DOCUMENT_SUFFIX_TOKENS = (
    "Memo",
    "Memorandum",
    "Report",
    "Letter",
    "Letters",
    "Article",
    "Articles",
    "Paper",
    "Papers",
    "Book",
    "Books",
    "Brief",
    "Briefing",
    "Briefings",
    "Slide",
    "Slides",
    "Document",
    "Documents",
    "Disclosure",
    "Disclosures",
    "Publication",
    "Publications",
    "Statement",
    "Testimony",
    "Affidavit",
    "Footage",
    "Recording",
    "Transcript",
    "Video",
    "Photographs",
    "Photograph",
    "Bulletin",
    "Volume",
    "Evaluation",
    "Assessment",
    "Estimate",
    "Summary",
    "Findings",
    "Analysis",
    "Dossier",
    "Treaty",
    "Notes",
    "Communique",
)

# These tokens preserve the node as an object/matter even if a document token
# appears - a "Reporting System" is a system, not a report.
_EXCLUSION_TOKENS = (
    "System",
    "Systems",
    "Programme",
    "Programmes",
    "Program",
    "Programs",
    "Centre",
    "Center",
    "Database",
    "Network",
    "Facility",
    "Service",
)

_DOC_SUFFIX_RE = re.compile(
    r"\b(?:" + "|".join(_DOCUMENT_SUFFIX_TOKENS) + r")s?$",
    re.IGNORECASE,
)
_DOC_WORD_RE = re.compile(
    r"\b(?:" + "|".join(_DOCUMENT_SUFFIX_TOKENS) + r")s?\b",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(
    r"\b(?:" + "|".join(_EXCLUSION_TOKENS) + r")\b",
    re.IGNORECASE,
)

# Specific names that should always be documents but might not match the
# generic patterns (the Navy UAP videos by their colloquial names).
_KNOWN_DOCUMENTS = {
    "FLIR1",
    "FLIR-1",
    "Gimbal",
    "Go-Fast",
    "Tic Tac FLIR",
    "Tic Tac FLIR Video",
}


_NODE_LINE_RE = re.compile(
    r"^### (?P<id>[a-f0-9-]{36}) (?P<type>object|matter): (?P<name>.+)$"
)


def is_document_name(name: str) -> bool:
    """Return True if a node's name looks like a document/artefact."""
    if name in _KNOWN_DOCUMENTS:
        return True
    if _EXCLUSION_RE.search(name):
        return False
    # Suffix match - "X Memo", "Y Report", "Some Briefing"
    if _DOC_SUFFIX_RE.search(name):
        return True
    # Whole-word document tokens inside the name (e.g. "Twining Letter 1947")
    if _DOC_WORD_RE.search(name):
        return True
    return False


def reclassify_documents_in_file(path: Path) -> int:
    """Rewrite `object/matter: X` lines to `document: X` in one extract file.

    Returns the number of nodes reclassified.
    """
    text = path.read_text()
    new_lines: list[str] = []
    reclassified = 0
    for line in text.splitlines():
        m = _NODE_LINE_RE.match(line)
        if m and is_document_name(m.group("name")):
            new_lines.append(f"### {m.group('id')} document: {m.group('name')}")
            reclassified += 1
        else:
            new_lines.append(line)
    if reclassified:
        path.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""))
    return reclassified


def reclassify_documents_in_dir(extracts_dir: Path) -> dict[str, int]:
    """Apply reclassify_documents_in_file across an extracts directory.

    Returns {filename: count} for files where anything changed.
    """
    results: dict[str, int] = {}
    for path in sorted(extracts_dir.glob("*.extract.md")):
        count = reclassify_documents_in_file(path)
        if count:
            results[path.name] = count
    return results
