"""Review-gate: decide whether a record may be digested.

Quality gate, not a cost gate (the cost concern went away with the subscription):
the knowledge graph must not be built from unreviewed material. A record is
digestible only when a reviewer has OBSERVED all of its content (Mark's rule:
"100% observed -> digestible; anything not observed -> skip"). Observation is
recorded by the workbench in a review-coverage sidecar
(`store/{hash}.review.json`, schema `anomalica/review-coverage/N`).

This module prefers a verdict the workbench computed (`digestible` /
`observed_coverage` fields, expected from schema /1) and falls back to computing
observed line-coverage from the raw spans for the current /0 sidecars. A record
with no sidecar at all is unreviewed, hence NOT digestible.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_TS_LINE = re.compile(r"^[0-9]{2}:[0-9]{2}:[0-9]{2}")


@dataclass
class Digestibility:
    digestible: bool
    observed_coverage: float  # 0.0-1.0 over content units
    total_units: int
    observed_units: int
    reason: str
    source: str  # "sidecar" (precomputed) | "computed" | "no-sidecar"


def _content_line_numbers(record_text: str) -> list[int]:
    """1-indexed line numbers of reviewable CONTENT - the transcript sentence
    lines for audio/video, else any non-blank, non-comment, non-frontmatter
    line. Frontmatter (the first --- ... --- block) is excluded."""
    lines = record_text.splitlines()
    # Skip the leading frontmatter block.
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
    ts_lines = [
        i
        for i in range(body_start + 1, len(lines) + 1)
        if _TS_LINE.match(lines[i - 1].strip())
    ]
    if ts_lines:
        return ts_lines  # timestamped transcript: the units are sentence lines
    return [
        i
        for i in range(body_start + 1, len(lines) + 1)
        if lines[i - 1].strip() and not lines[i - 1].strip().startswith("<!--")
    ]


def _observed_line_set(sidecar: dict) -> set[int]:
    obs: set[int] = set()
    for review in sidecar.get("reviews") or []:
        for span in review.get("spans") or []:
            if span.get("kind") == "observed":
                obs.update(range(span.get("from", 0), span.get("to", -1) + 1))
    return obs


def digestibility(
    record_text: str, sidecar: dict | None, threshold: float = 1.0
) -> Digestibility:
    """Decide whether a record is digestible.

    threshold is the observed-coverage fraction required (default 1.0 = whole
    record, per Mark's observed-only-100% rule).
    """
    if sidecar is None:
        return Digestibility(
            False, 0.0, 0, 0, "no review sidecar (unreviewed)", "no-sidecar"
        )

    # Prefer a verdict the workbench computed (forward-compat with schema /1).
    if "digestible" in sidecar:
        cov = float(sidecar.get("observed_coverage") or 0.0)
        return Digestibility(
            bool(sidecar["digestible"]),
            cov,
            int(sidecar.get("total_units") or 0),
            round(cov * float(sidecar.get("total_units") or 0)),
            "workbench-computed verdict",
            "sidecar",
        )

    # Fallback for legacy /0 sidecars with no verdict: compute observed
    # line-coverage from raw spans. APPROXIMATE and not authoritative - the
    # spans index line numbers in the body as it was at review time, which a
    # re-ingest can shift (bodies have moved to store/v1/), and 'to' is treated
    # as inclusive here. The workbench's computed verdict (above) is the source
    # of truth; this only keeps pre-/1 sidecars from silently reading as 0%.
    content = _content_line_numbers(record_text)
    if not content:
        return Digestibility(
            False, 0.0, 0, 0, "no reviewable content lines", "computed"
        )
    observed = _observed_line_set(sidecar)
    obs_content = [i for i in content if i in observed]
    cov = len(obs_content) / len(content)
    digestible = cov >= threshold
    reason = (
        "fully observed"
        if digestible
        else f"{len(content) - len(obs_content)} content units unobserved"
    )
    return Digestibility(
        digestible, round(cov, 4), len(content), len(obs_content), reason, "computed"
    )


def load_sidecar(record_md: Path, ingests_dir: Path) -> dict | None:
    """Find a record's review-coverage sidecar by its store hash.

    The .md may live under store/v1/ while the sidecar sits at the store root
    (the v1/ reorg moved record bodies but not sidecars), so search the
    colocated directory and walk up to the parent store dir.
    """
    target = record_md.resolve()
    fname = target.name.replace(".md", ".review.json")
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
