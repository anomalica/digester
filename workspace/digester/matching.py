"""Node matching for entity resolution.

Combines three strategies in order of preference:
1. Exact name and alias lookup (instant, perfect precision)
2. Levenshtein distance on names (catches typos, abbreviations, minor variants)
3. Claim-based embedding similarity (catches different names for the same thing)
"""

from __future__ import annotations

import sqlite3

from Levenshtein import ratio as levenshtein_ratio

from digester.database import find_node_by_name, get_nodes

# Minimum normalised Levenshtein similarity (0-1) to consider a fuzzy match.
# 0.75 catches "K. Day" vs "Kevin Day" but not "Kevin Day" vs "David Fravor".
FUZZY_NAME_THRESHOLD = 0.75

# Minimum word overlap ratio to even attempt Levenshtein comparison.
# Avoids comparing completely unrelated names.
MIN_WORD_OVERLAP = 0.3


def match_node(
    conn: sqlite3.Connection,
    name: str,
    node_type: str | None = None,
) -> tuple[str, str] | None:
    """Try to match a name to an existing node.

    Returns (node_id, match_method) or None if no match found.
    match_method is one of: "exact", "alias", "fuzzy".
    """
    # 1. Exact name match
    exact = find_node_by_name(conn, name, node_type)
    if exact:
        return exact.id, "exact"

    # 2. Fuzzy name match via Levenshtein
    candidates = get_nodes(conn, node_type=node_type)
    best_match = None
    best_score = 0.0

    name_lower = name.lower()
    name_words = set(name_lower.split())

    for candidate in candidates:
        candidate_lower = candidate.name.lower()
        candidate_words = set(candidate_lower.split())

        # Quick word overlap filter to avoid expensive comparisons
        if name_words and candidate_words:
            overlap = len(name_words & candidate_words)
            total = max(len(name_words), len(candidate_words))
            if overlap / total < MIN_WORD_OVERLAP:
                # Also check aliases for this candidate
                aliases = conn.execute(
                    "SELECT alias FROM aliases WHERE node_id = ?", (candidate.id,)
                ).fetchall()
                alias_match = False
                for (alias,) in aliases:
                    alias_sim = levenshtein_ratio(name_lower, alias.lower())
                    if alias_sim >= FUZZY_NAME_THRESHOLD and alias_sim > best_score:
                        best_match = candidate
                        best_score = alias_sim
                        alias_match = True
                if not alias_match:
                    continue

        # Levenshtein similarity on full name
        sim = levenshtein_ratio(name_lower, candidate_lower)
        if sim >= FUZZY_NAME_THRESHOLD and sim > best_score:
            best_match = candidate
            best_score = sim

    if best_match:
        return best_match.id, "fuzzy"

    return None
