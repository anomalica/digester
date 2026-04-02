"""Import extraction markdown files into the database.

This is a deterministic step with no AI involvement. The extraction
markdown is the source of truth; the database is derived from it.
"""

from __future__ import annotations

import sqlite3

from digester.database import (
    find_node_by_name,
    get_record_by_title,
    insert_alias,
    insert_claim,
    insert_node,
    insert_record,
)
from digester.matching import match_node
from digester.models import Claim, Node, Record


def import_extraction(
    conn: sqlite3.Connection,
    parsed: dict,
    section: str = "domain",
    lookup_conns: list[sqlite3.Connection] | None = None,
    on_progress: callable = None,
) -> dict:
    """Import a parsed extraction markdown into the database.

    Args:
        conn: database to write to
        parsed: output of parse_extraction_markdown()
        section: "domain" or "infrastructure" - which claims section to import
        lookup_conns: additional databases to check for existing nodes
        on_progress: callback for status messages

    Returns:
        dict with counts: nodes_created, nodes_matched, claims_created, record_id
    """
    log = on_progress or (lambda _: None)
    all_conns = [conn] + (lookup_conns or [])
    fm = parsed["frontmatter"]

    counts = {
        "nodes_created": 0,
        "nodes_matched": 0,
        "claims_created": 0,
        "record_id": None,
    }

    # Create or find record
    record_title = fm.get("record_title", "Untitled")
    existing_record = get_record_by_title(conn, record_title)
    if existing_record:
        record = existing_record
        log(f"  Existing record: {record.title} [{record.id[:8]}]")
    else:
        record = insert_record(
            conn,
            Record(
                id=fm.get("record_id"),
                title=record_title,
                reference=fm.get("record_reference"),
                date=str(fm["record_date"]) if fm.get("record_date") else None,
            ),
        )
        log(f"  Record: {record.title} [{record.id[:8]}]")
    counts["record_id"] = record.id

    # Build node map: name -> id (from the markdown's node definitions)
    # Match against existing nodes in database(s), create new ones as needed
    node_name_to_id = {}
    node_id_by_md_id = {}

    for node_def in parsed["nodes"]:
        name = node_def["name"]
        node_type = node_def["node_type"]
        md_id = node_def["id"]

        # Try to find existing node across all databases
        found = False
        for lookup_conn in all_conns:
            m = match_node(lookup_conn, name, node_type)
            if m:
                node_id = m[0]
                node_name_to_id[name] = node_id
                node_id_by_md_id[md_id] = node_id

                # Ensure node exists in target database if found elsewhere
                if lookup_conn is not conn:
                    if not find_node_by_name(conn, name, node_type):
                        insert_node(
                            conn,
                            Node(
                                id=node_id,
                                node_type=node_type,
                                name=name,
                                metadata=node_def.get("metadata"),
                            ),
                        )

                existing_name = lookup_conn.execute(
                    "SELECT name FROM nodes WHERE id = ?", (node_id,)
                ).fetchone()[0]
                if m[1] == "fuzzy" and name != existing_name:
                    insert_alias(conn, name, node_id)
                    log(f"  Matched: {name} -> {existing_name} [{node_id[:8]}]")
                else:
                    log(f"  Existing node: {name} ({node_type}) [{node_id[:8]}]")

                counts["nodes_matched"] += 1
                found = True
                break

        if not found:
            node = insert_node(
                conn,
                Node(
                    id=md_id,
                    node_type=node_type,
                    name=name,
                    metadata=node_def.get("metadata"),
                ),
            )
            node_name_to_id[name] = node.id
            node_id_by_md_id[md_id] = node.id
            log(f"  New node: {name} ({node_type}) [{node.id[:8]}]")
            counts["nodes_created"] += 1

    # Link record producer
    producer_name = fm.get("record_producer")
    if producer_name and producer_name in node_name_to_id:
        conn.execute(
            "UPDATE records SET producer_id = ? WHERE id = ?",
            (node_name_to_id[producer_name], record.id),
        )

    # Import claims from the specified section
    if section == "infrastructure":
        claims = parsed["infrastructure_claims"]
    else:
        claims = parsed["domain_claims"]

    for claim_def in claims:
        # Resolve node references by name
        ref_ids = []
        for ref_name in claim_def.get("node_references", []):
            if ref_name in node_name_to_id:
                ref_ids.append(node_name_to_id[ref_name])
            else:
                for lookup_conn in all_conns:
                    ref_match = match_node(lookup_conn, ref_name)
                    if ref_match:
                        ref_ids.append(ref_match[0])
                        node_name_to_id[ref_name] = ref_match[0]
                        break

        # Resolve speaker
        speaker_id = None
        speaker_name = claim_def.get("speaker")
        if speaker_name:
            if speaker_name in node_name_to_id:
                speaker_id = node_name_to_id[speaker_name]
            else:
                for lookup_conn in all_conns:
                    speaker_match = match_node(lookup_conn, speaker_name, "person")
                    if speaker_match:
                        speaker_id = speaker_match[0]
                        node_name_to_id[speaker_name] = speaker_match[0]
                        break

        insert_claim(
            conn,
            Claim(
                id=claim_def["id"],
                content=claim_def["content"],
                original_excerpt=claim_def.get("original_excerpt"),
                claim_type=claim_def["claim_type"],
                attestation=claim_def["attestation"],
                record_id=record.id,
                speaker_id=speaker_id,
                location_in_record=claim_def.get("location_in_record"),
                date=claim_def.get("date"),
                date_end=claim_def.get("date_end"),
                node_references=ref_ids,
            ),
        )
        counts["claims_created"] += 1

    conn.commit()
    return counts
