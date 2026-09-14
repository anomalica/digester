"""Reproducible generation/config authority and exact output validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from anomalica_common.pre_digest import PREP_VERSION, materialise, pre_digest_hash

from digester.extraction_config_registry import read_registry, register
from digester.generation import (
    ensure_manifest,
    read_manifest,
)
from digester.record_parser import parse_record


PRODUCTION_MODELS = ("sonnet", "opus")


class AuthorityError(ValueError):
    """Stored extraction authority or output does not match exact current inputs."""


def synchronise(digests_dir: Path) -> dict[str, str]:
    """Ensure generation 1 and both production model configurations are retained."""
    from digester.extract import effective_extraction_configuration

    ensure_manifest(digests_dir)
    fingerprints = {}
    for model in PRODUCTION_MODELS:
        configuration = effective_extraction_configuration(
            model,
            prep_version=PREP_VERSION,
            use_api=False,
        )
        fingerprints[model] = register(digests_dir, configuration)
    return fingerprints


def validate_output(digests_dir: Path, digest_path: Path, record_path: Path) -> dict:
    """Validate one canonical digest against exact committed authority and input."""
    root = digests_dir.resolve()
    output = digest_path.resolve()
    if not output.is_relative_to(root) or output.parent != root:
        raise AuthorityError("canonical digest path is outside the digest root")
    try:
        digest = yaml.safe_load(output.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise AuthorityError("canonical digest is unreadable") from exc
    if not isinstance(digest, dict) or digest.get("schema") != "anomalica/digest/1":
        raise AuthorityError("canonical digest schema is unsupported")
    try:
        current_generation = read_manifest(root)
        registry = read_registry(root)
    except ValueError as exc:
        raise AuthorityError(str(exc)) from exc
    if digest.get("extraction_generation") != current_generation:
        raise AuthorityError("canonical digest extraction generation is not current")
    config = digest.get("extraction_config")
    if config not in registry:
        raise AuthorityError("canonical digest extraction configuration is unresolved")

    try:
        record_text = record_path.read_text()
    except OSError as exc:
        raise AuthorityError("record is unreadable") from exc
    record = parse_record(record_text)
    record_hash = record.metadata.get("content_hash")
    if (digest.get("record") or {}).get("content_hash") != record_hash:
        raise AuthorityError("canonical digest targets a different record")
    expected_pre_digest = pre_digest_hash(materialise(record.body or ""))
    if (digest.get("pre_digest") or {}).get("sha256") != expected_pre_digest:
        raise AuthorityError("canonical digest pre-digest hash is not current")
    return {
        "digest_sha256": "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest(),
        "record_content_hash": record_hash,
        "extraction_generation": current_generation,
        "extraction_config": config,
    }
