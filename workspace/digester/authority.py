"""Reproducible generation/config authority and exact output validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from anomalica_common.identity import digest_record_snapshot_identity
from anomalica_common.pre_digest import (
    PREP_VERSION,
    materialise,
    pre_digest_hash,
    prepare_page_record,
    validate_digest2_bindings,
    validate_source_anchors,
)

from digester.extraction_config_registry import read_registry, register
from digester.generation import (
    ensure_manifest,
    read_manifest,
)
from digester.record_parser import parse_record
from digester.source_anchors import (
    digest_record_snapshot,
    is_digest2_record,
    record3_structure,
    validate_digest_record_projection,
)


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
    if not isinstance(digest, dict) or digest.get("schema") not in {
        "anomalica/digest/1",
        "anomalica/digest/2",
    }:
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
    structure = record3_structure(record)
    digest2 = is_digest2_record(structure)
    expected_schema = "anomalica/digest/2" if digest2 else "anomalica/digest/1"
    if digest.get("schema") != expected_schema:
        raise AuthorityError(
            f"canonical digest schema must be {expected_schema} for this Record"
        )
    record_hash = record.frontmatter.get("content_hash")
    if (digest.get("record") or {}).get("content_hash") != record_hash:
        raise AuthorityError("canonical digest targets a different record")
    if structure is not None:
        snapshot = digest_record_snapshot(record, structure)
        if digest.get("record_snapshot_sha256") != digest_record_snapshot_identity(
            snapshot
        ):
            raise AuthorityError("canonical digest Record snapshot is not current")
        try:
            validate_digest_record_projection(digest.get("record"), snapshot)
        except ValueError as exc:
            raise AuthorityError(
                f"canonical digest Record projection is invalid: {exc}"
            ) from exc
    if digest2:
        if structure is None:  # pragma: no cover - digest2 implies record/3
            raise AuthorityError("digest/2 has no record/3 structure")
        try:
            prepared = prepare_page_record(structure, record.body or "")
            validate_digest2_bindings(
                digest,
                prepared,
                snapshot.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
            for key in ("domain_claims", "infrastructure_claims"):
                for claim in digest.get(key) or []:
                    validate_source_anchors(claim.get("source_anchors") or [], prepared)
                    if "location" in claim:
                        raise AuthorityError(
                            "digest/2 claim retained a legacy scalar location"
                        )
        except (ValueError, TypeError) as exc:
            raise AuthorityError(
                f"canonical digest/2 binding is invalid: {exc}"
            ) from exc
    else:
        for key in ("domain_claims", "infrastructure_claims"):
            for claim in digest.get(key) or []:
                if claim.get("source_anchors") is not None:
                    raise AuthorityError(
                        "digest/1 claims cannot carry exact source anchors"
                    )
        expected_pre_digest = pre_digest_hash(materialise(record.body or ""))
        if (digest.get("pre_digest") or {}).get("sha256") != expected_pre_digest:
            raise AuthorityError("canonical digest pre-digest hash is not current")
    return {
        "digest_sha256": "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest(),
        "record_content_hash": record_hash,
        "extraction_generation": current_generation,
        "extraction_config": config,
    }
