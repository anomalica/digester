"""Manually maintained extraction generation for newly produced digests."""

from __future__ import annotations

import json
import re
from pathlib import Path


CURRENT_EXTRACTION_GENERATION = 1
MANIFEST_SCHEMA = "anomalica/digest-generation/1"


class GenerationManifestError(ValueError):
    """The corpus cannot supply a trustworthy current extraction generation."""


def read_manifest(digests_dir: Path) -> int:
    """Read and validate the generation authority from the corpus tree."""
    path = digests_dir / "digest-generation.json"
    try:
        document = json.loads(path.read_text())
    except OSError as exc:
        raise GenerationManifestError(
            f"missing or unreadable manifest: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise GenerationManifestError(f"malformed JSON manifest: {path}") from exc
    if not isinstance(document, dict) or document.get("schema") != MANIFEST_SCHEMA:
        raise GenerationManifestError(f"unsupported manifest schema: {path}")
    generation = document.get("current_generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 0
    ):
        raise GenerationManifestError(f"invalid current_generation in {path}")
    if generation != CURRENT_EXTRACTION_GENERATION:
        raise GenerationManifestError(
            f"manifest generation {generation} does not match digester generation "
            f"{CURRENT_EXTRACTION_GENERATION}"
        )
    return generation


def stamp(text: str) -> str:
    """Add the current generation beside the exact extraction configuration."""
    if re.search(r"(?m)^extraction_generation:", text):
        raise ValueError("digest already carries an extraction generation")
    marker = "extraction_config:"
    if marker not in text:
        raise ValueError("digest has no extraction_config to stamp")
    return text.replace(
        marker,
        f"extraction_generation: {CURRENT_EXTRACTION_GENERATION}\n{marker}",
        1,
    )
