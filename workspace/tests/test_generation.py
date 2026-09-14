from __future__ import annotations

import json
import subprocess

import pytest
import yaml
from anomalica_common.pre_digest import PREP_VERSION, materialise, pre_digest_hash
from click.testing import CliRunner

from digester import health
from digester.authority import AuthorityError, synchronise, validate_output
from digester.cli import main
from digester.generation import (
    CURRENT_EXTRACTION_GENERATION,
    GenerationManifestError,
    read_manifest,
    stamp,
)
from digester.extraction_config_registry import (
    ExtractionConfigRegistryError,
    REGISTRY_FILENAME,
    REGISTRY_SCHEMA,
    fingerprint,
    read_registry,
    register,
)
from digester.record_parser import parse_record


def test_stamp_places_generation_beside_exact_config():
    text = "schema: anomalica/digest/1\nextraction_config:\n  config: abc123\n"
    stamped = stamp(text)
    parsed = yaml.safe_load(stamped)
    assert parsed["extraction_generation"] == CURRENT_EXTRACTION_GENERATION
    assert parsed["extraction_config"] == {"config": "abc123"}
    assert stamped.index("extraction_generation:") < stamped.index("extraction_config:")


def test_stamp_refuses_to_infer_or_replace_an_existing_generation():
    with pytest.raises(ValueError, match="already carries"):
        stamp("extraction_generation: 0\nextraction_config: {}\n")


def test_generation_freshness_reports_explicit_states_and_distance():
    current = 3
    loaded = [
        ("current", {"extraction_generation": current}),
        ("lower", {"extraction_generation": current - 1}),
        ("missing", {}),
        ("future", {"extraction_generation": current + 1}),
        ("invalid", {"extraction_generation": "1"}),
        ("zero", {"extraction_generation": 0}),
    ]
    groups = health.extraction_generation_freshness(loaded, current)
    assert [row["digest"] for row in groups["current"]] == ["current"]
    assert groups["current"][0]["distance"] == 0
    assert [row["digest"] for row in groups["stale"]] == ["lower"]
    assert groups["stale"][0]["distance"] == 1
    assert {row["digest"] for row in groups["unknown"]} == {
        "missing",
        "future",
        "invalid",
        "zero",
    }
    assert groups["invalid"] == []


def test_health_fails_closed_when_the_manifest_is_unavailable():
    groups = health.extraction_generation_freshness(
        [
            ("apparently-current", {"extraction_generation": 1}),
            ("malformed", {"extraction_generation": "1"}),
            ("missing", {}),
        ],
        None,
    )
    assert groups["current"] == []
    assert groups["invalid"] == []
    assert {row["digest"] for row in groups["unknown"]} == {
        "apparently-current",
        "malformed",
        "missing",
    }
    assert {row["reason"] for row in groups["unknown"]} == {"manifest_unavailable"}


def test_manifest_is_the_validated_corpus_authority(tmp_path):
    (tmp_path / "digest-generation.json").write_text(
        json.dumps(
            {
                "schema": "anomalica/digest-generation/1",
                "current_generation": CURRENT_EXTRACTION_GENERATION,
            }
        )
    )
    assert read_manifest(tmp_path) == CURRENT_EXTRACTION_GENERATION


def test_authority_reproducibly_includes_current_sonnet_and_opus(tmp_path):
    fingerprints = synchronise(tmp_path)

    assert set(fingerprints) == {"sonnet", "opus"}
    assert read_manifest(tmp_path) == CURRENT_EXTRACTION_GENERATION
    registry = read_registry(tmp_path)
    assert set(registry) == set(fingerprints.values())
    assert {
        configuration["model"]["resolved"] for configuration in registry.values()
    } == {"claude-sonnet-5", "claude-opus-5"}

    before = {
        path.name: path.read_bytes()
        for path in (
            tmp_path / "digest-generation.json",
            tmp_path / REGISTRY_FILENAME,
        )
    }
    assert synchronise(tmp_path) == fingerprints
    assert {
        path.name: path.read_bytes()
        for path in (
            tmp_path / "digest-generation.json",
            tmp_path / REGISTRY_FILENAME,
        )
    } == before


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"schema": "wrong", "current_generation": 1},
        {"schema": "anomalica/digest-generation/1", "current_generation": 0},
        {"schema": "anomalica/digest-generation/1", "current_generation": True},
        {"schema": "anomalica/digest-generation/1", "current_generation": 2},
    ],
)
def test_manifest_rejects_unsupported_invalid_or_inconsistent_state(tmp_path, document):
    (tmp_path / "digest-generation.json").write_text(json.dumps(document))
    with pytest.raises(GenerationManifestError):
        read_manifest(tmp_path)


def test_configuration_freshness_requires_a_resolvable_contract_fingerprint():
    registered = "sha256:" + "a" * 64
    unregistered = "sha256:" + "b" * 64
    groups = health.extraction_config_freshness(
        [
            ("resolvable", {"extraction_config": registered}),
            ("unregistered", {"extraction_config": unregistered}),
            ("missing", {}),
            ("legacy", {"extraction_config": {"config": "12345678"}}),
            ("short", {"extraction_config": "sha256:abcd"}),
        ],
        {registered: {"configuration_schema": "anomalica/digest-extraction-config/1"}},
    )
    assert [row["digest"] for row in groups["resolvable"]] == ["resolvable"]
    assert [row["digest"] for row in groups["unregistered"]] == ["unregistered"]
    assert [row["digest"] for row in groups["missing"]] == ["missing"]
    assert {row["digest"] for row in groups["invalid"]} == {"legacy", "short"}


def _configuration(name: str) -> dict:
    return {
        "configuration_schema": "anomalica/digest-extraction-config/1",
        "model": {"resolved": name},
    }


def test_registry_round_trips_an_exact_configuration(tmp_path):
    configuration = _configuration("model-a")
    key = register(tmp_path, configuration)
    assert key == fingerprint(configuration)
    assert read_registry(tmp_path) == {key: configuration}


def test_register_safely_migrates_an_unwrapped_legacy_registry(tmp_path):
    old = _configuration("old")
    old_key = fingerprint(old)
    (tmp_path / REGISTRY_FILENAME).write_text(json.dumps({old_key: old}))

    new = _configuration("new")
    new_key = register(tmp_path, new)

    document = json.loads((tmp_path / REGISTRY_FILENAME).read_text())
    assert document["schema"] == REGISTRY_SCHEMA
    assert set(document["configurations"]) == {old_key, new_key}


def test_register_refuses_to_overwrite_an_invalid_legacy_registry(tmp_path):
    path = tmp_path / REGISTRY_FILENAME
    path.write_text(json.dumps({"sha256:" + "a" * 64: _configuration("wrong")}))
    before = path.read_text()
    with pytest.raises(ExtractionConfigRegistryError, match="does not match"):
        register(tmp_path, _configuration("new"))
    assert path.read_text() == before


def test_digest_loader_reports_malformed_documents_instead_of_skipping_them(tmp_path):
    (tmp_path / "broken.yaml").write_text("[unterminated")
    issues = []
    assert health.load_digests(tmp_path, issues) == []
    assert issues[0]["digest"] == "broken"
    assert issues[0]["issue"] == "malformed_yaml"


def test_health_reports_generation_and_input_denominators(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_SURVIVAL_CACHE", tmp_path / "cache.json")
    digests = tmp_path / "digests"
    store = tmp_path / "ingests" / "store"
    records = tmp_path / "ingests" / "by-name"
    digests.mkdir()
    store.mkdir(parents=True)
    records.mkdir()
    body = "The witness saw a light."
    record_hash = "c" * 64
    record = (
        f"---\nschema: anomalica/record/1\ncontent_hash: sha256:{record_hash}\n"
        f"source_type: web\n---\n\n{body}\n"
    )
    (store / f"{record_hash}.md").write_text(record)
    (records / "record.md").symlink_to(f"../store/{record_hash}.md")
    configuration = _configuration("model-a")
    config_hash = register(digests, configuration)
    (digests / "digest-generation.json").write_text(
        json.dumps(
            {
                "schema": "anomalica/digest-generation/1",
                "current_generation": CURRENT_EXTRACTION_GENERATION,
            }
        )
    )
    (digests / "record.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "anomalica/digest/1",
                "extraction_generation": CURRENT_EXTRACTION_GENERATION,
                "extraction_config": config_hash,
                "record": {
                    "content_hash": f"sha256:{record_hash}",
                    "medium": "web",
                },
                "pre_digest": {
                    "sha256": pre_digest_hash(materialise(parse_record(record).body)),
                    "prep_version": PREP_VERSION,
                },
            }
        )
    )

    result = CliRunner().invoke(
        main,
        [
            "health",
            "--digests",
            str(digests),
            "--store",
            str(store),
            "--records",
            str(records),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "current   1/1" in result.output
    assert "Pre-digest input binding (denominator 1 digests)" in result.output


def test_digest_and_authority_remain_current_after_clean_clone(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    fingerprints = synchronise(source)
    body = "The witness saw a light."
    record_hash = "c" * 64
    record = tmp_path / "record.md"
    record.write_text(
        f"---\nschema: anomalica/record/1\ncontent_hash: sha256:{record_hash}\n"
        f"source_type: web\n---\n\n{body}\n"
    )
    digest_path = source / "record.yaml"
    digest_path.write_text(
        yaml.safe_dump(
            {
                "schema": "anomalica/digest/1",
                "extraction_generation": CURRENT_EXTRACTION_GENERATION,
                "extraction_config": fingerprints["sonnet"],
                "record": {"content_hash": f"sha256:{record_hash}"},
                "pre_digest": {
                    "sha256": pre_digest_hash(
                        materialise(parse_record(record.read_text()).body)
                    ),
                    "prep_version": PREP_VERSION,
                },
            }
        )
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "add",
            "--",
            *sorted(path.name for path in source.iterdir() if path.is_file()),
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(source), str(checkout)], check=True)

    result = validate_output(checkout, checkout / "record.yaml", record)

    assert result["extraction_generation"] == CURRENT_EXTRACTION_GENERATION
    assert result["extraction_config"] == fingerprints["sonnet"]
    assert result["record_content_hash"] == f"sha256:{record_hash}"


@pytest.mark.parametrize("broken", ["generation", "config", "pre_digest"])
def test_output_is_current_only_when_exact_authority_and_input_match(tmp_path, broken):
    fingerprints = synchronise(tmp_path)
    body = "The witness saw a light."
    record_hash = "d" * 64
    record = tmp_path.parent / f"{tmp_path.name}-record.md"
    record.write_text(
        f"---\ncontent_hash: sha256:{record_hash}\nsource_type: web\n---\n\n{body}\n"
    )
    document = {
        "schema": "anomalica/digest/1",
        "extraction_generation": CURRENT_EXTRACTION_GENERATION,
        "extraction_config": fingerprints["sonnet"],
        "record": {"content_hash": f"sha256:{record_hash}"},
        "pre_digest": {
            "sha256": pre_digest_hash(
                materialise(parse_record(record.read_text()).body)
            ),
            "prep_version": PREP_VERSION,
        },
    }
    if broken == "generation":
        document["extraction_generation"] += 1
    elif broken == "config":
        document["extraction_config"] = "sha256:" + "0" * 64
    else:
        document["pre_digest"]["sha256"] = "0" * 64
    digest_path = tmp_path / "record.yaml"
    digest_path.write_text(yaml.safe_dump(document))

    with pytest.raises(AuthorityError):
        validate_output(tmp_path, digest_path, record)
