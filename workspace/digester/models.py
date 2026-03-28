from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


class NodeType(str, Enum):
    person = "person"
    organisation = "organisation"
    place = "place"
    event = "event"
    matter = "matter"
    object = "object"
    record = "record"
    claim = "claim"


class ClaimType(str, Enum):
    observation = "observation"
    testimony = "testimony"
    hearsay = "hearsay"
    opinion = "opinion"
    measurement = "measurement"
    administrative = "administrative"


class AttestationLevel(str, Enum):
    first_hand = "first_hand"
    second_hand = "second_hand"
    third_hand = "third_hand"


# --- Graph nodes ---


class Node(BaseModel):
    """Base for all nodes in the knowledge graph."""

    id: str = Field(default_factory=_uuid)
    node_type: NodeType
    name: str
    metadata: dict | None = None
    created_at: datetime | None = None
    retired_at: datetime | None = None


class Record(BaseModel):
    """A specific artefact containing information. Pointer to original material."""

    id: str = Field(default_factory=_uuid)
    title: str
    reference: str | None = None
    date: str | None = None
    producer_id: str | None = None
    metadata: dict | None = None
    created_at: datetime | None = None


class Claim(BaseModel):
    """An atomic assertion extracted from a record."""

    id: str = Field(default_factory=_uuid)
    content: str
    original_excerpt: str | None = None
    claim_type: ClaimType
    attestation: AttestationLevel
    record_id: str
    speaker_id: str | None = None
    location_in_record: str | None = None
    date: str | None = None
    date_end: str | None = None
    node_references: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict | None = None
    created_at: datetime | None = None


# --- Extraction pipeline models ---


class ExtractedNode(BaseModel):
    """A node identified during extraction, before deduplication."""

    name: str
    node_type: NodeType
    metadata: dict | None = None


class ExtractedClaim(BaseModel):
    """A claim identified during extraction, before storage."""

    content: str
    original_excerpt: str | None = None
    claim_type: ClaimType
    attestation: AttestationLevel
    speaker: str | None = None
    location_in_record: str | None = None
    date: str | None = None
    date_end: str | None = None
    node_references: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    """Output of the extraction pipeline for a single record."""

    record_title: str
    record_reference: str | None = None
    record_date: str | None = None
    record_producer: str | None = None
    nodes: list[ExtractedNode] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
