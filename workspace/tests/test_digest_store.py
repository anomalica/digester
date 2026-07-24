"""Variant-aware digest storage (ADR 0039 amended): re-digests never overwrite."""

from digester import digest_store as ds

ACTIVE = [
    {
        "pass": "nodes",
        "id": "nodes",
        "version": "v2",
        "sha256": "aaa",
        "file": "nodes.txt",
    },
    {
        "pass": "claims",
        "id": "claims",
        "version": "v2",
        "sha256": "bbb",
        "file": "claims.txt",
    },
]
OVERRIDE = [
    {
        "pass": "nodes",
        "id": "nodes",
        "version": "override",
        "sha256": "ccc",
        "file": "/tmp/x",
    },
    {
        "pass": "claims",
        "id": "claims",
        "version": "v2",
        "sha256": "bbb",
        "file": "claims.txt",
    },
]


def test_prompt_sha8_distinguishes_prompt_sets():
    assert ds.prompt_sha8(ACTIVE) != ds.prompt_sha8(OVERRIDE)
    assert len(ds.prompt_sha8(ACTIVE)) == 8


def test_is_active_prompt():
    assert ds.is_active_prompt(ACTIVE) is True
    assert ds.is_active_prompt(OVERRIDE) is False
    assert ds.is_active_prompt([]) is False


def test_variant_path_carries_model_and_prompt(tmp_path):
    vp = ds.variant_path(
        tmp_path, "2021-05-17-video-navy", "deepseek/deepseek-v4-pro", ACTIVE
    )
    assert vp.parent == tmp_path / "variants" / "2021-05-17-video-navy"
    assert vp.name == f"deepseek-deepseek-v4-pro.{ds.prompt_sha8(ACTIVE)}.yaml"


def test_active_run_writes_variant_and_canonical(tmp_path):
    w = ds.write_digest(tmp_path, "rec", "DIGEST", "haiku", ACTIVE)
    assert w["variant"].read_text() == "DIGEST"
    assert w["canonical"] == tmp_path / "records" / "rec.yaml"
    assert w["canonical"].read_text() == "DIGEST"


def test_override_run_writes_variant_only(tmp_path):
    w = ds.write_digest(tmp_path, "rec", "EXPERIMENT", "haiku", OVERRIDE)
    assert w["variant"].exists()
    assert w["canonical"] is None
    assert not (tmp_path / "records" / "rec.yaml").exists()


def test_variant_only_flag_skips_canonical(tmp_path):
    w = ds.write_digest(tmp_path, "rec", "SIDE", "haiku", ACTIVE, variant_only=True)
    assert w["variant"].exists()
    assert w["canonical"] is None


def test_prompt_tune_preserves_prior_variant(tmp_path):
    # same model, different prompt -> two variants coexist (the whole point)
    v1 = ds.write_digest(tmp_path, "rec", "OLD", "haiku", ACTIVE)["variant"]
    tuned = [dict(ACTIVE[0], sha256="zzz"), ACTIVE[1]]
    v2 = ds.write_digest(tmp_path, "rec", "NEW", "haiku", tuned)["variant"]
    assert v1 != v2
    assert v1.read_text() == "OLD" and v2.read_text() == "NEW"
    # identical model+prompt is a redo: overwrites its own file only
    v1b = ds.write_digest(tmp_path, "rec", "REDO", "haiku", ACTIVE)["variant"]
    assert v1b == v1 and v1.read_text() == "REDO"


def test_versioned_symlink_writes_the_de_versioned_dir(tmp_path):
    """A .vN input names the same record as its unversioned form; both must land
    in one variant dir, or a record's variants fragment across two."""
    from digester import digest_store

    prov = [{"pass": "nodes", "version": "v1"}, {"pass": "claims", "version": "v1"}]
    v = digest_store.variant_path(tmp_path, "jon-stewart.v2", "haiku", prov)
    c = digest_store.canonical_path(tmp_path, "jon-stewart.v2")

    assert v.parent.name == "jon-stewart"  # not "jon-stewart.v2"
    assert c.name == "jon-stewart.yaml"
    # and the unversioned form lands in the identical place
    assert (
        digest_store.variant_path(tmp_path, "jon-stewart", "haiku", prov).parent
        == v.parent
    )


def test_opencode_model_can_never_write_the_canonical(tmp_path):
    # opencode cannot enforce an output schema, so its claims may omit required
    # provenance fields and reference nodes outside Pass A's enum. Structurally
    # advisory output must not reach the graph or the public site even if a caller
    # forgets --variant-only.
    prov = [{"version": "v3", "sha256": "abc"}]
    written = ds.write_digest(
        tmp_path, "rec", "text: yes\n", "opencode-go/glm-5.2", prov, variant_only=False
    )
    assert written["canonical"] is None
    assert written["variant"].exists()

    # ... while an ordinary model with the active prompt still does.
    written2 = ds.write_digest(tmp_path, "rec", "text: yes\n", "haiku", prov)
    assert written2["canonical"] is not None
