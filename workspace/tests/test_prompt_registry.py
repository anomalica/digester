"""Versioned prompt registry + digest prompt-provenance stamping."""

import hashlib

from anomalica_common.digest import two_pass_result_to_yaml
from digester import extract, prompt_registry


def test_active_prompts_resolve_from_files():
    for pid in ("nodes", "claims"):
        text, prov = prompt_registry.resolve_prompt(pid)
        assert prov.id == pid
        assert prov.version == "v2"
        assert prov.sha256 == hashlib.sha256(text.encode()).hexdigest()
        assert (prompt_registry.PROMPTS_DIR / prov.file).read_text() == text


def test_loaders_return_registry_content():
    assert extract._nodes_prompt() == prompt_registry.prompt_text("nodes")
    assert extract._claims_prompt_template() == prompt_registry.prompt_text("claims")


def test_env_override_is_recorded_not_silent(tmp_path, monkeypatch):
    f = tmp_path / "p.txt"
    f.write_text("CUSTOM PROMPT")
    monkeypatch.setenv("DIGESTER_NODES_PROMPT_FILE", str(f))
    text, prov = prompt_registry.resolve_prompt("nodes", "DIGESTER_NODES_PROMPT_FILE")
    assert text == "CUSTOM PROMPT"
    assert prov.version == "override"
    assert prov.sha256 == hashlib.sha256(b"CUSTOM PROMPT").hexdigest()
    assert prov.file == str(f)


def test_historical_versions_registered_for_attribution():
    reg = prompt_registry._registry()
    # pre-two-pass prompts retired (kept only for attribution)
    for retired in ("extraction", "infrastructure", "terminology"):
        assert reg[retired]["active"] is None
        assert len(reg[retired]["versions"]) >= 1
    # active lineages carry earlier states too
    assert len(reg["nodes"]["versions"]) > 1
    for pid in ("nodes", "claims", "extraction", "infrastructure", "terminology"):
        for meta in reg[pid]["versions"].values():
            assert (prompt_registry.PROMPTS_DIR / meta["file"]).exists()


def test_prompt_provenance_shape():
    prov = extract.prompt_provenance()
    assert [p["pass"] for p in prov] == ["nodes", "claims"]
    for p in prov:
        assert set(p) == {"pass", "id", "version", "sha256", "file"}


def test_digest_yaml_stamps_prompts():
    result = {
        "nodes": [],
        "claims": [],
        "prompt_provenance": extract.prompt_provenance(),
    }
    out = two_pass_result_to_yaml(result, record_title="T", model="haiku")
    assert "prompts:" in out
    assert "id: nodes" in out and "id: claims" in out
