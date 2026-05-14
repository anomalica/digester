import sqlite3
from pathlib import Path

from digester.database import (
    init_db,
    insert_alias,
    insert_claim,
    insert_node,
    insert_record,
)
from digester.models import AttestationLevel, Claim, ClaimType, Node, NodeType, Record
from digester.obsidian_export import (
    _safe_filename,
    _safe_wikilink,
    export_to_obsidian,
)


def _db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_safe_filename_strips_forbidden_chars():
    assert _safe_filename("Foo/Bar:Baz?") == "Foo_Bar_Baz_"
    assert _safe_filename("  .  ") == "_unnamed" or _safe_filename("  .  ") == "_"


def test_safe_wikilink_strips_brackets():
    assert _safe_wikilink("Foo [Bar] | Baz") == "Foo Bar  Baz"


def test_export_writes_records_and_nodes(tmp_path: Path):
    conn = _db()
    fravor = insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    nimitz = insert_node(conn, Node(node_type=NodeType.object, name="USS Nimitz"))
    insert_alias(conn, "Cdr Fravor", fravor.id)
    rec = insert_record(
        conn, Record(title="Fravor Hearing", date="2023-07-26", producer_id=fravor.id)
    )
    insert_claim(
        conn,
        Claim(
            content="Fravor observed the Tic Tac.",
            claim_type=ClaimType.observation,
            attestation=AttestationLevel.first_hand,
            record_id=rec.id,
            speaker_id=fravor.id,
            location_in_record="page 1",
            date="2004-11-14",
            node_references=[fravor.id, nimitz.id],
            original_excerpt="I saw the Tic Tac.",
        ),
    )
    conn.commit()

    out = tmp_path / "vault"
    counts = export_to_obsidian(out, conn)

    assert counts == {"records": 1, "nodes": 2, "claims": 1}
    assert (out / "Records").is_dir()
    assert (out / "Nodes").is_dir()

    record_files = list((out / "Records").glob("*.md"))
    assert len(record_files) == 1
    record_text = record_files[0].read_text()
    assert "[[David Fravor]]" in record_text
    assert "[[USS Nimitz]]" in record_text
    assert "Fravor observed the Tic Tac." in record_text
    assert "> I saw the Tic Tac." in record_text

    node_files = list((out / "Nodes").glob("*.md"))
    assert len(node_files) == 2
    fravor_file = next(p for p in node_files if "Fravor" in p.name)
    fravor_text = fravor_file.read_text()
    assert "type: person" in fravor_text
    assert "Cdr Fravor" in fravor_text  # alias


def test_export_merges_infra_nodes_by_name(tmp_path: Path):
    domain = _db()
    infra = _db()
    insert_node(domain, Node(node_type=NodeType.person, name="Luis Elizondo"))
    insert_node(infra, Node(node_type=NodeType.person, name="Luis Elizondo"))
    insert_node(infra, Node(node_type=NodeType.record, name="60 Minutes Episode"))

    out = tmp_path / "vault"
    counts = export_to_obsidian(out, domain, infra)
    # Two distinct people-only entries would be wrong; Elizondo should appear once
    assert counts["nodes"] == 2
    names = {p.stem for p in (out / "Nodes").glob("*.md")}
    assert "Luis Elizondo" in names
    assert "60 Minutes Episode" in names


def test_export_handles_filename_collisions(tmp_path: Path):
    conn = _db()
    insert_node(conn, Node(node_type=NodeType.person, name="John Smith"))
    insert_node(conn, Node(node_type=NodeType.organisation, name="John Smith"))
    conn.commit()
    out = tmp_path / "vault"
    export_to_obsidian(out, conn)
    files = list((out / "Nodes").glob("*.md"))
    assert len(files) == 2  # collision-handled, both present
