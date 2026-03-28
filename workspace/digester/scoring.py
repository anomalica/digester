"""Algorithmic evidence scoring for claims.

Scores are computed from graph properties, not assigned by humans.
The methodology is transparent and reproducible.

Scoring factors:
- Number of independent records corroborating a claim
- Attestation depth (first-hand > second-hand > third-hand)
- Claim type weight (measurement > testimony > observation > hearsay > opinion)
- Whether claims have been contradicted
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from digester.models import AttestationLevel, ClaimType


ATTESTATION_WEIGHTS = {
    AttestationLevel.first_hand: 1.0,
    AttestationLevel.second_hand: 0.6,
    AttestationLevel.third_hand: 0.3,
}

CLAIM_TYPE_WEIGHTS = {
    ClaimType.measurement: 0.9,
    ClaimType.testimony: 0.8,
    ClaimType.observation: 0.75,
    ClaimType.administrative: 0.7,
    ClaimType.hearsay: 0.4,
    ClaimType.opinion: 0.3,
}


@dataclass
class ScoreBreakdown:
    score: float
    record_count: int
    attestation: str
    claim_type: str
    base_weight: float
    corroboration_factor: float
    components: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [
            f"{self.record_count} record(s)",
            self.attestation,
            self.claim_type,
            f"score: {self.score:.2f}",
        ]
        return ", ".join(parts)


def score_claim(conn: sqlite3.Connection, claim_id: str) -> ScoreBreakdown:
    """Calculate an evidence score for a single claim."""
    row = conn.execute(
        "SELECT claim_type, attestation, record_id FROM claims WHERE id = ?",
        (claim_id,),
    ).fetchone()
    if row is None:
        return ScoreBreakdown(
            score=0.0,
            record_count=0,
            attestation="unknown",
            claim_type="unknown",
            base_weight=0.0,
            corroboration_factor=0.0,
        )

    claim_type = ClaimType(row[0])
    attestation = AttestationLevel(row[1])

    # Base weight from claim type and attestation
    type_weight = CLAIM_TYPE_WEIGHTS.get(claim_type, 0.5)
    attestation_weight = ATTESTATION_WEIGHTS.get(attestation, 0.5)
    base_weight = type_weight * attestation_weight

    # Count corroborating records (records containing similar claims)
    # For now, count records that share the same claim content
    record_count = conn.execute(
        "SELECT COUNT(DISTINCT record_id) FROM claims WHERE content = (SELECT content FROM claims WHERE id = ?)",
        (claim_id,),
    ).fetchone()[0]

    # Noisy-OR corroboration: each independent record increases confidence
    if record_count <= 1:
        corroboration_factor = 1.0
    else:
        product = 1.0
        for _ in range(record_count):
            product *= 1.0 - base_weight
        corroboration_factor = (1.0 - product) / base_weight if base_weight > 0 else 1.0

    final = min(1.0, base_weight * corroboration_factor)

    return ScoreBreakdown(
        score=final,
        record_count=record_count,
        attestation=attestation.value,
        claim_type=claim_type.value,
        base_weight=base_weight,
        corroboration_factor=corroboration_factor,
        components={
            "type_weight": type_weight,
            "attestation_weight": attestation_weight,
            "base": base_weight,
            "corroboration": corroboration_factor,
        },
    )


def score_all_claims(conn: sqlite3.Connection) -> dict[str, ScoreBreakdown]:
    """Score every claim in the database."""
    rows = conn.execute("SELECT id FROM claims").fetchall()
    return {row[0]: score_claim(conn, row[0]) for row in rows}


def tier_label(score: float) -> str:
    """Convert a numeric score to a human-readable tier label."""
    if score >= 0.85:
        return "verified"
    if score >= 0.65:
        return "probable"
    if score >= 0.40:
        return "disputed"
    if score >= 0.20:
        return "legend"
    if score >= 0.10:
        return "misidentified"
    return "hoax"
