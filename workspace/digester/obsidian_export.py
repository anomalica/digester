"""Export the knowledge graph as an Obsidian-navigable markdown vault.

One file per record (with all claims, each linking to referenced nodes via
`[[wikilinks]]`) and one stub per node. Obsidian's backlinks panel surfaces
the incoming claim references on each node without us having to enumerate
them.

Designed for manual inspection: open the output directory in Obsidian and
click between records and nodes to verify the extraction.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

# Characters forbidden in Obsidian / Windows / macOS filenames
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
# Obsidian wikilinks break on these inner chars
_WIKILINK_FORBIDDEN = re.compile(r"[\[\]|#^]")


def _safe_filename(name: str) -> str:
    cleaned = _FORBIDDEN.sub("_", name).strip().rstrip(".")
    return cleaned[:200] if cleaned else "_unnamed"


def _safe_wikilink(name: str) -> str:
    return _WIKILINK_FORBIDDEN.sub("", name).strip()


def _node_link(name: str | None) -> str:
    if not name:
        return "_"
    return f"[[{_safe_wikilink(name)}]]"


def _gather_nodes(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return {node_id: {name, node_type, metadata, aliases}} for active nodes."""
    nodes = {}
    rows = conn.execute(
        "SELECT id, node_type, name, metadata FROM nodes WHERE retired_at IS NULL"
    ).fetchall()
    for nid, ntype, name, _meta in rows:
        nodes[nid] = {"name": name, "node_type": ntype, "aliases": []}
    alias_rows = conn.execute("SELECT alias, node_id FROM aliases").fetchall()
    for alias, nid in alias_rows:
        if nid in nodes and alias != nodes[nid]["name"]:
            nodes[nid]["aliases"].append(alias)
    return nodes


def _gather_records(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, date, producer_id, reference FROM records"
    ).fetchall()
    return [
        {
            "id": rid,
            "title": title,
            "date": date,
            "producer_id": producer_id,
            "reference": reference,
        }
        for rid, title, date, producer_id, reference in rows
    ]


def _gather_claims_for_record(conn: sqlite3.Connection, record_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, content, original_excerpt, claim_type, attestation, speaker_id,
               location_in_record, date, date_end, confidence
        FROM claims
        WHERE record_id = ?
        ORDER BY date IS NULL, date, location_in_record
        """,
        (record_id,),
    ).fetchall()
    claims = []
    for row in rows:
        cid = row[0]
        ref_ids = conn.execute(
            "SELECT node_id FROM claim_node_refs WHERE claim_id = ?", (cid,)
        ).fetchall()
        claims.append(
            {
                "id": cid,
                "content": row[1],
                "original_excerpt": row[2],
                "claim_type": row[3],
                "attestation": row[4],
                "speaker_id": row[5],
                "location_in_record": row[6],
                "date": row[7],
                "date_end": row[8],
                "confidence": row[9],
                "ref_ids": [r[0] for r in ref_ids],
            }
        )
    return claims


def _format_claim(claim: dict, nodes: dict[str, dict]) -> str:
    speaker = (
        nodes.get(claim["speaker_id"], {}).get("name") if claim["speaker_id"] else None
    )
    refs = [nodes[rid]["name"] for rid in claim["ref_ids"] if rid in nodes]

    lines = [f"### `{claim['claim_type']}` / `{claim['attestation']}`"]
    lines.append("")
    lines.append(claim["content"])
    lines.append("")
    meta_lines = []
    if speaker:
        meta_lines.append(f"- **Speaker:** {_node_link(speaker)}")
    if claim["date"]:
        date_str = claim["date"]
        if claim["date_end"] and claim["date_end"] != claim["date"]:
            date_str += f" to {claim['date_end']}"
        meta_lines.append(f"- **Date:** {date_str}")
    if claim["location_in_record"]:
        meta_lines.append(f"- **Location:** {claim['location_in_record']}")
    if refs:
        meta_lines.append(f"- **Refs:** {', '.join(_node_link(r) for r in refs)}")
    if claim["confidence"] < 1.0:
        meta_lines.append(f"- **Confidence:** {claim['confidence']:.2f}")
    if meta_lines:
        lines.extend(meta_lines)
        lines.append("")
    if claim["original_excerpt"]:
        for excerpt_line in claim["original_excerpt"].splitlines() or [""]:
            lines.append(f"> {excerpt_line}")
        lines.append("")
    lines.append("")
    return "\n".join(lines)


def _format_record_file(
    record: dict, claims: list[dict], nodes: dict[str, dict]
) -> str:
    producer = (
        nodes.get(record["producer_id"], {}).get("name")
        if record["producer_id"]
        else None
    )

    fm = ["---", "type: record", f"id: {record['id']}"]
    if record["date"]:
        fm.append(f"date: {record['date']}")
    if producer:
        fm.append(f"producer: {producer}")
    if record["reference"]:
        fm.append(f"reference: {record['reference']}")
    fm.append("---")
    fm.append("")
    fm.append(f"# {record['title']}")
    fm.append("")
    if producer:
        fm.append(f"Producer: {_node_link(producer)}")
        fm.append("")
    fm.append(f"## Claims ({len(claims)})")
    fm.append("")
    for c in claims:
        fm.append(_format_claim(c, nodes))
    return "\n".join(fm)


def _format_node_file(node: dict, node_id: str) -> str:
    lines = ["---", f"type: {node['node_type']}", f"id: {node_id}"]
    if node["aliases"]:
        lines.append("aliases:")
        for a in sorted(set(node["aliases"])):
            lines.append(f"  - {a}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {node['name']}")
    lines.append("")
    lines.append(f"`{node['node_type']}`")
    lines.append("")
    lines.append(
        "> [!info] Backlinks below show every claim that references this node."
    )
    lines.append("")
    return "\n".join(lines)


def export_to_obsidian(
    out_dir: Path,
    domain_conn: sqlite3.Connection,
    infra_conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    """Write a navigable Obsidian vault to `out_dir`.

    Returns counts: {records, nodes, claims}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records_dir = out_dir / "Records"
    nodes_dir = out_dir / "Nodes"
    records_dir.mkdir(exist_ok=True)
    nodes_dir.mkdir(exist_ok=True)

    nodes = _gather_nodes(domain_conn)
    if infra_conn is not None:
        # Merge infra nodes by name to avoid duplicate stubs for the same entity.
        infra_nodes = _gather_nodes(infra_conn)
        domain_names = {n["name"] for n in nodes.values()}
        for nid, node in infra_nodes.items():
            if node["name"] not in domain_names:
                nodes[nid] = node
                domain_names.add(node["name"])

    seen_node_files: set[str] = set()
    for nid, node in nodes.items():
        fname = _safe_filename(node["name"]) + ".md"
        if fname in seen_node_files:
            fname = f"{_safe_filename(node['name'])} ({node['node_type']}).md"
        seen_node_files.add(fname)
        (nodes_dir / fname).write_text(_format_node_file(node, nid))

    record_count = 0
    claim_count = 0
    seen_record_files: set[str] = set()
    for conn in [domain_conn] + ([infra_conn] if infra_conn else []):
        for record in _gather_records(conn):
            claims = _gather_claims_for_record(conn, record["id"])
            claim_count += len(claims)
            date_prefix = (record["date"] + " - ") if record["date"] else ""
            fname = _safe_filename(date_prefix + record["title"]) + ".md"
            if fname in seen_record_files:
                fname = (
                    _safe_filename(date_prefix + record["title"] + " (infrastructure)")
                    + ".md"
                )
            seen_record_files.add(fname)
            (records_dir / fname).write_text(_format_record_file(record, claims, nodes))
            record_count += 1

    return {
        "records": record_count,
        "nodes": len(nodes),
        "claims": claim_count,
    }


def export_iter(records_dir: Path, nodes_dir: Path) -> Iterable[Path]:
    """Yield every file written by an export (handy for tests)."""
    yield from records_dir.glob("*.md")
    yield from nodes_dir.glob("*.md")
