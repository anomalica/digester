import sqlite3

from digester.database import init_db, insert_alias, insert_node
from digester.matching import match_node
from digester.models import Node, NodeType


def _db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_exact_match():
    conn = _db()
    node = insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    conn.commit()

    result = match_node(conn, "David Fravor", "person")
    assert result is not None
    assert result[0] == node.id
    assert result[1] == "exact"


def test_alias_match():
    conn = _db()
    node = insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    insert_alias(conn, "Fravor", node.id)
    conn.commit()

    result = match_node(conn, "Fravor", "person")
    assert result is not None
    assert result[0] == node.id


def test_fuzzy_match():
    conn = _db()
    insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    conn.commit()

    result = match_node(conn, "David Favor", "person")  # typo
    assert result is not None
    assert result[1] == "fuzzy"


def test_no_match():
    conn = _db()
    insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    conn.commit()

    result = match_node(conn, "Kevin Day", "person")
    assert result is None


def test_type_filtering():
    conn = _db()
    insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    conn.commit()

    # Wrong type should not match
    result = match_node(conn, "David Fravor", "organisation")
    assert result is None

    # Right type matches
    result = match_node(conn, "David Fravor", "person")
    assert result is not None


def test_no_type_matches_any():
    conn = _db()
    node = insert_node(conn, Node(node_type=NodeType.person, name="David Fravor"))
    conn.commit()

    result = match_node(conn, "David Fravor")
    assert result is not None
    assert result[0] == node.id
