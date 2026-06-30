"""Versioned extraction prompts and their provenance.

Prompts are managed files under `prompts/`; the active version of each id is the
source of truth. Every extraction records which prompt file/version/hash it used
(the registry default, or a `DIGESTER_*_PROMPT_FILE` override), so a digest is
attributable to an exact prompt - mirrors the assembler's auditable-assembly
pattern (ADR 0010).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_REGISTRY: dict | None = None


@dataclass(frozen=True)
class PromptProvenance:
    """Which prompt produced a pass: content-addressed by sha256."""

    id: str
    version: str
    sha256: str
    file: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "sha256": self.sha256,
            "file": self.file,
        }


def _registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = yaml.safe_load((PROMPTS_DIR / "registry.yaml").read_text())[
            "prompts"
        ]
    return _REGISTRY


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def resolve_prompt(
    prompt_id: str, env_var: str | None = None
) -> tuple[str, PromptProvenance]:
    """Return `(text, provenance)` for a prompt id.

    A `DIGESTER_*_PROMPT_FILE` override (named by `env_var`) wins and is recorded
    as version ``override`` with the file's own hash - never silent. Otherwise the
    registry's active version file is used.
    """
    if env_var:
        override = os.environ.get(env_var)
        if override and os.path.exists(override):
            text = Path(override).read_text()
            return text, PromptProvenance(prompt_id, "override", _sha(text), override)
    entry = _registry()[prompt_id]
    version = entry["active"]
    fname = entry["versions"][version]["file"]
    text = (PROMPTS_DIR / fname).read_text()
    return text, PromptProvenance(prompt_id, version, _sha(text), fname)


def prompt_text(prompt_id: str, env_var: str | None = None) -> str:
    return resolve_prompt(prompt_id, env_var)[0]
