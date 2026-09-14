"""Canonical resolver for exact extraction-configuration fingerprints."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path


REGISTRY_FILENAME = "extraction-configurations.json"
REGISTRY_SCHEMA = "anomalica/digest-extraction-config-registry/1"
CONFIGURATION_SCHEMA = "anomalica/digest-extraction-config/1"
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")


class ExtractionConfigRegistryError(ValueError):
    """The registry cannot safely resolve or retain exact configurations."""


def canonical_json(configuration: dict) -> str:
    if not isinstance(configuration, dict):
        raise ExtractionConfigRegistryError("configuration must be a mapping")
    if configuration.get("configuration_schema") != CONFIGURATION_SCHEMA:
        raise ExtractionConfigRegistryError(
            "unsupported extraction configuration schema"
        )
    return json.dumps(
        configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def fingerprint(configuration: dict) -> str:
    return (
        "sha256:" + hashlib.sha256(canonical_json(configuration).encode()).hexdigest()
    )


def _validated_configurations(document: object, path: Path) -> dict[str, dict]:
    if not isinstance(document, dict):
        raise ExtractionConfigRegistryError(f"registry is not a mapping: {path}")
    if document.get("schema") == REGISTRY_SCHEMA:
        configurations = document.get("configurations")
    elif "schema" not in document:
        # A pre-schema registry was a direct fingerprint-to-configuration map.
        # Read it without changing it; the next successful registration wraps it
        # in the canonical document while retaining every exact entry.
        configurations = document
    else:
        raise ExtractionConfigRegistryError(f"unsupported registry schema: {path}")
    if not isinstance(configurations, dict):
        raise ExtractionConfigRegistryError(
            f"registry configurations are invalid: {path}"
        )
    validated: dict[str, dict] = {}
    for key, configuration in configurations.items():
        if not isinstance(key, str) or not _FINGERPRINT.fullmatch(key):
            raise ExtractionConfigRegistryError(f"invalid fingerprint key in {path}")
        try:
            actual = fingerprint(configuration)
        except ExtractionConfigRegistryError as exc:
            raise ExtractionConfigRegistryError(
                f"invalid configuration for {key}: {path}"
            ) from exc
        if actual != key:
            raise ExtractionConfigRegistryError(
                f"configuration does not match fingerprint {key}: {path}"
            )
        validated[key] = configuration
    return validated


def read_registry(root: Path) -> dict[str, dict]:
    path = root / REGISTRY_FILENAME
    try:
        document = json.loads(path.read_text())
    except OSError as exc:
        raise ExtractionConfigRegistryError(
            f"missing or unreadable registry: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ExtractionConfigRegistryError(f"malformed JSON registry: {path}") from exc
    return _validated_configurations(document, path)


def register(root: Path, configuration: dict) -> str:
    """Atomically retain CONFIGURATION under its exact SHA-256 identity."""
    key = fingerprint(configuration)
    root.mkdir(parents=True, exist_ok=True)
    path = root / REGISTRY_FILENAME
    lock_fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if path.exists():
            try:
                document = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ExtractionConfigRegistryError(
                    f"cannot update invalid registry: {path}"
                ) from exc
            configurations = _validated_configurations(document, path)
        else:
            configurations = {}
        existing = configurations.get(key)
        if existing is not None and canonical_json(existing) != canonical_json(
            configuration
        ):
            raise ExtractionConfigRegistryError(f"fingerprint collision for {key}")
        configurations[key] = configuration
        output = {
            "schema": REGISTRY_SCHEMA,
            "configurations": dict(sorted(configurations.items())),
        }
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
            tmp.replace(path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ExtractionConfigRegistryError(
                f"cannot write registry: {path}"
            ) from exc
    finally:
        os.close(lock_fd)
    return key
