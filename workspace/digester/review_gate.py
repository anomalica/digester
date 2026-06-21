"""Review-gate: decide whether a record may be digested.

Quality gate, not a cost gate: the knowledge graph must not be built from
unreviewed material. The digestibility RULE (the threshold, the content-unit
definition, the sidecar reading) is single-sourced in anomalica_common.review_gate
so the digester and the workbench compute the same verdict. This module is the
digester's file/sidecar wrappers around that core: locating a record's
review-coverage sidecar and the path-resolving entry point used by the `coverage`
CLI command.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from anomalica_common.review_gate import Digestibility, digestibility

__all__ = ["Digestibility", "digestibility", "load_sidecar", "assess_record"]

# A record body is {hash}.md or {hash}.v2.md (a processing-version infix), but
# its review sidecar is keyed by the BARE content hash: {hash}.review.json. Strip
# any version infix when deriving the sidecar name, else v2 records never find
# their sidecar and read as unreviewed.
_BODY_SUFFIX = re.compile(r"(\.v\d+)?\.md$")


def load_sidecar(record_md: Path, ingests_dir: Path) -> dict | None:
    """Find a record's review-coverage sidecar by its store hash.

    The .md may live under store/v1/ while the sidecar sits at the store root
    (the v1/ reorg moved record bodies but not sidecars), so search the
    colocated directory and walk up to the parent store dir.
    """
    target = record_md.resolve()
    fname = _BODY_SUFFIX.sub(".review.json", target.name)
    for d in (target.parent, target.parent.parent):
        sidecar = d / fname
        if sidecar.exists():
            try:
                return json.loads(sidecar.read_text())
            except (OSError, json.JSONDecodeError):
                return None
    return None


def assess_record(
    record_md: Path, ingests_dir: Path, threshold: float = 1.0
) -> Digestibility:
    text = record_md.resolve().read_text()
    return digestibility(text, load_sidecar(record_md, ingests_dir), threshold)
