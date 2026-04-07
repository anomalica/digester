import sqlite3

from digester.database import (
    find_node_by_name,
    get_claims_for_node,
    get_corroborations,
    get_independent_source_count,
    get_stats,
    init_db,
    insert_alias,
    insert_claim,
    insert_corroboration,
    insert_node,
    insert_record,
)
from digester.models import AttestationLevel, Claim, ClaimType, Node, NodeType, Record


def _db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_insert_and_get_node():
    conn = _db()
    node = insert_node(conn, Node(node_type=NodeType.person, name="Alice"))
    assert node.id
    assert node.created_at

    found = find_node_by_name(conn, "Alice", "person")
    assert found is not None
    assert found.name == "Alice"


def test_alias_lookup():
    conn = _db()
    node = insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    insert_alias(conn, "Fravor", node.id)
    insert_alias(conn, "CDR Fravor", node.id)

    assert find_node_by_name(conn, "Fravor", "person").id == node.id
    assert find_node_by_name(conn, "CDR Fravor", "person").id == node.id
    assert find_node_by_name(conn, "Unknown Person", "person") is None


def test_insert_record_and_claim():
    conn = _db()
    node = insert_node(conn, Node(node_type=NodeType.person, name="Alice"))
    record = insert_record(conn, Record(title="Test Record"))
    insert_claim(
        conn,
        Claim(
            content="Alice saw something.",
            claim_type=ClaimType.observation,
            attestation=AttestationLevel.first_hand,
            record_id=record.id,
            speaker_id=node.id,
            node_references=[node.id],
        ),
    )
    conn.commit()

    claims = get_claims_for_node(conn, node.id)
    assert len(claims) == 1
    assert claims[0].content == "Alice saw something."
    assert claims[0].node_references == [node.id]


def test_corroboration_and_independent_sources():
    conn = _db()
    alice = insert_node(conn, Node(node_type=NodeType.person, name="Alice"))
    bob = insert_node(conn, Node(node_type=NodeType.person, name="Bob"))
    rec1 = insert_record(conn, Record(title="Record 1"))
    rec2 = insert_record(conn, Record(title="Record 2"))

    # Same claim from different speakers in different records
    c1 = insert_claim(
        conn,
        Claim(
            content="The sky is blue.",
            claim_type=ClaimType.observation,
            attestation=AttestationLevel.first_hand,
            record_id=rec1.id,
            speaker_id=alice.id,
        ),
    )
    c2 = insert_claim(
        conn,
        Claim(
            content="The sky is blue.",
            claim_type=ClaimType.observation,
            attestation=AttestationLevel.first_hand,
            record_id=rec2.id,
            speaker_id=bob.id,
        ),
    )
    conn.commit()

    insert_corroboration(conn, c1.id, c2.id, 0.99)
    conn.commit()

    corrs = get_corroborations(conn, c1.id)
    assert len(corrs) == 1

    # Different speakers = 2 independent sources
    assert get_independent_source_count(conn, c1.id) == 2


def test_same_speaker_not_independent():
    conn = _db()
    alice = insert_node(conn, Node(node_type=NodeType.person, name="Alice"))
    rec1 = insert_record(conn, Record(title="Record 1"))
    rec2 = insert_record(conn, Record(title="Record 2"))

    # Same claim, same speaker, different records
    c1 = insert_claim(
        conn,
        Claim(
            content="I saw it.",
            claim_type=ClaimType.observation,
            attestation=AttestationLevel.first_hand,
            record_id=rec1.id,
            speaker_id=alice.id,
        ),
    )
    c2 = insert_claim(
        conn,
        Claim(
            content="I saw it.",
            claim_type=ClaimType.observation,
            attestation=AttestationLevel.first_hand,
            record_id=rec2.id,
            speaker_id=alice.id,
        ),
    )
    conn.commit()

    insert_corroboration(conn, c1.id, c2.id, 0.99)
    conn.commit()

    # Same speaker = 1 independent source despite 2 records
    assert get_independent_source_count(conn, c1.id) == 1


def test_stats():
    conn = _db()
    insert_node(conn, Node(node_type=NodeType.person, name="Alice"))
    insert_node(conn, Node(node_type=NodeType.organisation, name="ACME"))
    rec = insert_record(conn, Record(title="Test"))
    insert_claim(
        conn,
        Claim(
            content="Test claim.",
            claim_type=ClaimType.administrative,
            attestation=AttestationLevel.first_hand,
            record_id=rec.id,
        ),
    )
    conn.commit()

    s = get_stats(conn)
    assert s["nodes"] == 2
    assert s["active_nodes"] == 2
    assert s["records"] == 1
    assert s["claims"] == 1
    assert s["by_type"]["person"] == 1
    assert s["by_type"]["organisation"] == 1
