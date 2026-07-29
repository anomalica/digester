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

__all__ = [
    "Digestibility",
    "digestibility",
    "load_sidecar",
    "assess_record",
    "review_provenance",
]

# A sidecar written by something automated must SAY SO. `by` is a free-text
# address with no machine/human discriminator, so "reviewed" stops meaning "a
# human looked" the moment anything automated learns to write there - and an AI
# triage pass is coming. The burden therefore sits on the new writer to identify
# itself rather than on every reader to guess: a positive marker wins, and only
# in its absence does a sidecar imply a human.
_MACHINE_MARKERS = ("anomalica/triage/", "triage_model", "produced_by_model")


def review_provenance(record_md: Path, ingests_dir: Path) -> dict:
    """What is known about who observed this record, for stamping into a digest.

    Review has never been enforced in the extract path, so a digest from
    unreviewed OCR is otherwise indistinguishable from one a reviewer read line
    by line. This records the difference rather than deciding on it.

    `state` is the field a consumer filters on and is machine-derivable:
    ``machine`` when the sidecar identifies an automated writer, ``human`` when a
    sidecar exists without one, ``none`` when there is no sidecar at all.
    """
    sidecar = load_sidecar(record_md, ingests_dir)
    if sidecar is None:
        return {"state": "none", "sidecar": "absent"}
    blob = json.dumps(sidecar).lower()
    state = "machine" if any(m in blob for m in _MACHINE_MARKERS) else "human"
    cov = sidecar.get("observed_coverage")
    out = {"state": state, "sidecar": "present"}
    if cov is not None:
        out["observed_coverage"] = round(float(cov), 4)
    return out


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
