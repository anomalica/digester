from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from digester.models import Claim, Node, Record

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL,
    retired_at TEXT
);

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    reference TEXT,
    date TEXT,
    producer_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    attestation TEXT NOT NULL,
    record_id TEXT NOT NULL REFERENCES records(id),
    speaker_id TEXT,
    location_in_record TEXT,
    date TEXT,
    date_end TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_node_refs (
    claim_id TEXT NOT NULL REFERENCES claims(id),
    node_id TEXT NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (claim_id, node_id)
);

CREATE TABLE IF NOT EXISTS aliases (
    alias TEXT NOT NULL,
    node_id TEXT NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (alias, node_id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_claims_record ON claims(record_id);
CREATE INDEX IF NOT EXISTS idx_claims_speaker ON claims(speaker_id);
CREATE INDEX IF NOT EXISTS idx_claim_refs_node ON claim_node_refs(node_id);
CREATE INDEX IF NOT EXISTS idx_aliases_node ON aliases(node_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)


def insert_node(conn: sqlite3.Connection, node: Node) -> Node:
    now = _now()
    metadata_json = json.dumps(node.metadata) if node.metadata else None
    conn.execute(
        "INSERT INTO nodes (id, node_type, name, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
        (node.id, node.node_type.value, node.name, metadata_json, now),
    )
    return node.model_copy(update={"created_at": datetime.fromisoformat(now)})


def get_node(conn: sqlite3.Connection, node_id: str) -> Node | None:
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        return None
    return _row_to_node(row)


def get_nodes(conn: sqlite3.Connection, node_type: str | None = None) -> list[Node]:
    if node_type:
        rows = conn.execute(
            "SELECT * FROM nodes WHERE node_type = ? AND retired_at IS NULL",
            (node_type,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM nodes WHERE retired_at IS NULL").fetchall()
    return [_row_to_node(row) for row in rows]


def find_node_by_name(
    conn: sqlite3.Connection, name: str, node_type: str | None = None
) -> Node | None:
    if node_type:
        row = conn.execute(
            "SELECT * FROM nodes WHERE name = ? AND node_type = ? AND retired_at IS NULL",
            (name, node_type),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM nodes WHERE name = ? AND retired_at IS NULL", (name,)
        ).fetchone()
    if row:
        return _row_to_node(row)
    if node_type:
        alias_row = conn.execute(
            "SELECT n.* FROM nodes n JOIN aliases a ON a.node_id = n.id "
            "WHERE a.alias = ? AND n.node_type = ? AND n.retired_at IS NULL",
            (name, node_type),
        ).fetchone()
    else:
        alias_row = conn.execute(
            "SELECT n.* FROM nodes n JOIN aliases a ON a.node_id = n.id "
            "WHERE a.alias = ? AND n.retired_at IS NULL",
            (name,),
        ).fetchone()
    if alias_row:
        return _row_to_node(alias_row)
    return None


def insert_alias(conn: sqlite3.Connection, alias: str, node_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO aliases (alias, node_id) VALUES (?, ?)",
        (alias, node_id),
    )


def insert_record(conn: sqlite3.Connection, record: Record) -> Record:
    now = _now()
    metadata_json = json.dumps(record.metadata) if record.metadata else None
    conn.execute(
        "INSERT INTO records (id, title, reference, date, producer_id, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            record.id,
            record.title,
            record.reference,
            record.date,
            record.producer_id,
            metadata_json,
            now,
        ),
    )
    return record.model_copy(update={"created_at": datetime.fromisoformat(now)})


def get_record(conn: sqlite3.Connection, record_id: str) -> Record | None:
    row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def get_record_by_title(conn: sqlite3.Connection, title: str) -> Record | None:
    row = conn.execute("SELECT * FROM records WHERE title = ?", (title,)).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def insert_claim(conn: sqlite3.Connection, claim: Claim) -> Claim:
    now = _now()
    metadata_json = json.dumps(claim.metadata) if claim.metadata else None
    conn.execute(
        "INSERT INTO claims (id, content, claim_type, attestation, record_id, speaker_id, "
        "location_in_record, date, date_end, confidence, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            claim.id,
            claim.content,
            claim.claim_type.value,
            claim.attestation.value,
            claim.record_id,
            claim.speaker_id,
            claim.location_in_record,
            claim.date,
            claim.date_end,
            claim.confidence,
            metadata_json,
            now,
        ),
    )
    for node_id in claim.node_references:
        conn.execute(
            "INSERT OR IGNORE INTO claim_node_refs (claim_id, node_id) VALUES (?, ?)",
            (claim.id, node_id),
        )
    return claim.model_copy(update={"created_at": datetime.fromisoformat(now)})


def get_claims_for_node(conn: sqlite3.Connection, node_id: str) -> list[Claim]:
    rows = conn.execute(
        "SELECT c.* FROM claims c "
        "JOIN claim_node_refs r ON r.claim_id = c.id "
        "WHERE r.node_id = ?",
        (node_id,),
    ).fetchall()
    claims = []
    for row in rows:
        claim = _row_to_claim(row)
        claim.node_references = _get_claim_refs(conn, claim.id)
        claims.append(claim)
    return claims


def get_claims_for_record(conn: sqlite3.Connection, record_id: str) -> list[Claim]:
    rows = conn.execute(
        "SELECT * FROM claims WHERE record_id = ?", (record_id,)
    ).fetchall()
    claims = []
    for row in rows:
        claim = _row_to_claim(row)
        claim.node_references = _get_claim_refs(conn, claim.id)
        claims.append(claim)
    return claims


def get_stats(conn: sqlite3.Connection) -> dict:
    stats = {}
    for table in ("nodes", "records", "claims", "claim_node_refs", "aliases"):
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        stats[table] = row[0]
    active_nodes = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE retired_at IS NULL"
    ).fetchone()
    stats["active_nodes"] = active_nodes[0]
    type_counts = conn.execute(
        "SELECT node_type, COUNT(*) FROM nodes WHERE retired_at IS NULL GROUP BY node_type"
    ).fetchall()
    stats["by_type"] = {row[0]: row[1] for row in type_counts}
    return stats


def _get_claim_refs(conn: sqlite3.Connection, claim_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT node_id FROM claim_node_refs WHERE claim_id = ?", (claim_id,)
    ).fetchall()
    return [row[0] for row in rows]


def _row_to_node(row: tuple) -> Node:
    return Node(
        id=row[0],
        node_type=row[1],
        name=row[2],
        metadata=json.loads(row[3]) if row[3] else None,
        created_at=datetime.fromisoformat(row[4]),
        retired_at=datetime.fromisoformat(row[5]) if row[5] else None,
    )


def _row_to_record(row: tuple) -> Record:
    return Record(
        id=row[0],
        title=row[1],
        reference=row[2],
        date=row[3],
        producer_id=row[4],
        metadata=json.loads(row[5]) if row[5] else None,
        created_at=datetime.fromisoformat(row[6]),
    )


def _row_to_claim(row: tuple) -> Claim:
    return Claim(
        id=row[0],
        content=row[1],
        claim_type=row[2],
        attestation=row[3],
        record_id=row[4],
        speaker_id=row[5],
        location_in_record=row[6],
        date=row[7],
        date_end=row[8],
        confidence=row[9],
        metadata=json.loads(row[10]) if row[10] else None,
        created_at=datetime.fromisoformat(row[11]),
    )
