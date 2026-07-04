"""Variant-aware digest storage (ADR 0039, amended 2026-07-04).

A re-digest never overwrites a prior one: each (model, prompt) combination is a
separate variant file under ``variants/{friendly-name}/``, so tuning a prompt
and re-running the same model keeps both outputs for comparison. Identical
model+prompt overwrites only its own variant file - correct redo semantics.

The canonical record digest at ``records/{friendly-name}.yaml`` is what the
assimilator imports. It is updated by PRODUCTION runs only: a run using a
DIGESTER_*_PROMPT_FILE override (a prompt-provenance entry with
``version: "override"``) writes its variant and never touches the canonical, so
experimental prompts from the tuning loop never leak into the graph. Reconciliation
supersedes latest-written canonical later (ADR 0039).

0039 named variants ``{model-id}-{version}.yaml`` (model version only); that
predates prompt-provenance. Prompt tunes are the common re-digest, so the variant
key carries the prompt identity too - otherwise a prompt tune on the same model
would overwrite exactly what we mean to preserve.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def prompt_sha8(prompt_provenance: list[dict] | None) -> str:
    """8-char digest of the passes' combined sha256s - the prompt identity that
    distinguishes one prompt set from another in a variant filename."""
    joined = "".join(p.get("sha256", "") for p in (prompt_provenance or []))
    return hashlib.sha256(joined.encode()).hexdigest()[:8]


def _safe(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", model)


def is_active_prompt(prompt_provenance: list[dict] | None) -> bool:
    """True for a production run: every pass used the registered active prompt.
    Any ``override`` pass marks an experiment (variant-only, never canonical)."""
    prov = prompt_provenance or []
    return bool(prov) and all(p.get("version") != "override" for p in prov)


def variant_path(
    digests_root: Path,
    friendly_name: str,
    model: str,
    prompt_provenance: list[dict] | None,
) -> Path:
    return (
        digests_root
        / "variants"
        / friendly_name
        / f"{_safe(model)}.{prompt_sha8(prompt_provenance)}.yaml"
    )


def canonical_path(digests_root: Path, friendly_name: str) -> Path:
    return digests_root / "records" / f"{friendly_name}.yaml"


def write_digest(
    digests_root: Path,
    friendly_name: str,
    text: str,
    model: str,
    prompt_provenance: list[dict] | None,
    variant_only: bool = False,
) -> dict:
    """Write the model-variant (always) and, for a production run not marked
    variant-only, update the canonical (latest-written). Returns the paths
    written: ``{"variant": Path, "canonical": Path | None}``.
    """
    vp = variant_path(digests_root, friendly_name, model, prompt_provenance)
    vp.parent.mkdir(parents=True, exist_ok=True)
    vp.write_text(text)
    written = {"variant": vp, "canonical": None}
    if not variant_only and is_active_prompt(prompt_provenance):
        cp = canonical_path(digests_root, friendly_name)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(text)
        written["canonical"] = cp
    return written
