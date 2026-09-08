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


# A record and its versioned re-ingest are ONE record: `jon-stewart` and
# `jon-stewart.v2` name the same source at different ingest revisions, and their
# digests must share a variant dir or a record's variants fragment across two
# (the opus-v3 before-state sat under `.../jon-stewart/` while a run on the v2
# symlink wrote into `.../jon-stewart.v2/`). The `.vN` is an ingest-revision
# marker on the symlink name, never part of the record's identity - strip it so
# the layout keys on the record, not the revision.
_INGEST_VERSION_SUFFIX = re.compile(r"\.v\d+$")


def _de_version(friendly_name: str) -> str:
    return _INGEST_VERSION_SUFFIX.sub("", friendly_name)


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
    run_label: str | None = None,
) -> Path:
    """Path for a model+prompt variant, optionally widened by a run label.

    Without a label the key is (model, prompt sha) - so a re-run of the same
    configuration correctly OVERWRITES its own variant. That is right for redo
    semantics and wrong for a DELIBERATE REPEAT: measuring run-to-run variance
    needs two artefacts of the identical configuration, and without a label the
    second silently overwrites the first, leaving one file and nothing to compare
    after paying for both. `run_label` makes a repeat a first-class artefact rather
    than a one-off hack run outside the queue.
    """
    stem = f"{_safe(model)}.{prompt_sha8(prompt_provenance)}"
    if run_label:
        stem += f".{_safe(run_label)}"
    return digests_root / "variants" / _de_version(friendly_name) / f"{stem}.yaml"


_PROMPT_SHA = re.compile(r"^[0-9a-f]{8}$")


def split_variant_stem(stem: str) -> tuple[str, str, str]:
    """A variant filename back into (model, prompt sha, run label).

    Splitting on the first dot loses two things and both matter. A model name
    contains dots - `openai-gpt-5.6-luna` became "openai-gpt-5" in the grade
    table, so two providers' rows could collide. And the prompt sha is what
    separates a comparison from a confound: a grid part-built on one claims
    prompt and part on another ranks the prompt, not the model. The sha is the
    one fixed-shape segment, so find it and read outwards.
    """
    parts = stem.split(".")
    for i, part in enumerate(parts):
        if _PROMPT_SHA.match(part):
            return ".".join(parts[:i]), part, ".".join(parts[i + 1 :])
    return stem, "", ""


def canonical_path(digests_root: Path, friendly_name: str) -> Path:
    return digests_root / f"{_de_version(friendly_name)}.yaml"


def write_digest(
    digests_root: Path,
    friendly_name: str,
    text: str,
    model: str,
    prompt_provenance: list[dict] | None,
    variant_only: bool = False,
    run_label: str | None = None,
) -> dict:
    """Write the model-variant (always) and, for a production run not marked
    variant-only, update the canonical (latest-written). Returns the paths
    written: ``{"variant": Path, "canonical": Path | None}``.
    """
    vp = variant_path(digests_root, friendly_name, model, prompt_provenance, run_label)
    vp.parent.mkdir(parents=True, exist_ok=True)
    written = {"variant": vp, "canonical": None}
    # An opencode run is COMPARISON-ONLY and can never become canonical, enforced
    # here rather than left to the caller: opencode cannot enforce an output schema
    # (prompt-embedded only), so its claims may omit required provenance fields and
    # may reference nodes outside Pass A's enum. Structurally advisory output must
    # not reach the graph or the public site no matter which flags a caller passes.
    from anomalica_common.llm import is_opencode_model

    if is_opencode_model(model):
        variant_only = True
    # A labelled run is a deliberate repeat for measurement, never the production
    # artefact - it must not move the canonical even under the active prompt.
    if run_label:
        variant_only = True
    # A production run's artefact lands in BOTH trees - the variant store records
    # what this (model, prompt) produced, the canonical names the chosen one - and
    # the two copies were byte-identical with nothing saying which was which. Any
    # cost figure summed over variants/ therefore counted production runs as
    # comparison work, silently: a 12.6M-token book digest read as variant-lane
    # consumption and inflated a per-book cost measurement by ~2x before anyone
    # noticed. Stamping the KIND of run makes the artefact answer that on its own,
    # rather than requiring every consumer to cross-reference records/ and
    # remember why.
    is_production = not variant_only and is_active_prompt(prompt_provenance)
    kind = "production" if is_production else "comparison"
    stamped = f"run_kind: {kind}\n{text}"
    vp.write_text(stamped)
    if is_production:
        cp = canonical_path(digests_root, friendly_name)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(stamped)
        written["canonical"] = cp
    return written
