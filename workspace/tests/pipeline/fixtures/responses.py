"""Canned model responses for the two fixture documents, and the resolver that
serves them in place of a real model call.

`digester.extract.call_with_document` is the only model call either live
extraction pass makes, so replacing it is the whole substitution. `response_for`
has that function's signature: it reads the pass off the schema the caller
built and the document off a marker string, and returns the JSON text the
transport would have returned.

Every response is validated at import time against the schema the code builds at
RUNTIME - `NODES_SCHEMA_V2` and `build_claims_schema_v2(node_names)` - never
against a copy pasted in here. A fixture that encodes a shape the model is
constrained never to emit would prove nothing, and a copied constant stops
tracking the original the day someone edits one of them.

Quotes are verbatim from the record bodies, with one deliberate exception: the
second claim of document B is a paraphrase, so the unalignable branch of
location normalisation is exercised.
"""

from __future__ import annotations

import json

from .documents import DOCUMENT_A, DOCUMENT_B

# --- Document A -------------------------------------------------------------

NODES_A = {
    "main_subject": "Skerrivore Point radar return",
    "nodes": [
        {"name": "Dr Helena Marsh", "node_type": "person"},
        {"name": "Ivo Rennick", "node_type": "person"},
        {"name": "Group Captain Aled Furze", "node_type": "person"},
        {"name": "Northern Reach Observatory", "node_type": "organisation"},
        {"name": "Coastal Air Defence Command", "node_type": "organisation"},
        {"name": "Skerrivore Point", "node_type": "place"},
        {"name": "Whitchurch Down", "node_type": "place"},
        {
            "name": "Skerrivore Point radar return",
            "node_type": "event",
            "metadata": {"date_start": "1994-03-14", "date_end": "1994-03-14"},
        },
        {"name": "the boxed Skerrivore Point tapes", "node_type": "object"},
        {"name": "Skerrivore Point incident file", "node_type": "document"},
    ],
    "codenames_to_resolve": [
        {
            "codename": "the consignment",
            "refers_to": "the boxed Skerrivore Point tapes",
        }
    ],
    "acronyms": [],
    "extraction_complete": True,
}

CLAIMS_A = {
    "claims": [
        {
            # first_hand: the speaker is the origin and nothing stands between
            # them. Carries all four ref roles.
            "content": (
                "Helena Marsh logged an object at 23:40 on 14 March 1994 "
                "tracking north over Skerrivore Point, which the plate "
                "solution placed at four thousand metres."
            ),
            "original_excerpt": (
                "I logged the object myself at eleven forty that night, "
                "tracking north over Skerrivore Point, and the plate solution "
                "put it at four thousand metres."
            ),
            "category": "domain",
            "claim_type": "observation",
            "provenance_chain": {
                "origin_kind": "speaker",
                "origin": "Dr Helena Marsh",
                "relay": [],
            },
            "attribution_in_text": True,
            "attestation": "first_hand",
            "speaker": "Dr Helena Marsh",
            "location_in_record": "around 20 seconds in",
            "date": "1994-03-14",
            "node_references": [
                {"name": "Skerrivore Point radar return", "role": "subject"},
                {"name": "Dr Helena Marsh", "role": "participant"},
                {"name": "Skerrivore Point", "role": "setting"},
                {"name": "Northern Reach Observatory", "role": "mentioned"},
            ],
            "confidence": 1.0,
        },
        {
            # second_hand by an empty relay: a named origin told the speaker
            # directly.
            "content": (
                "Coastal Air Defence Command held the same radar return on its "
                "scope for eleven minutes."
            ),
            "original_excerpt": (
                "Coastal Air Defence Command had held the same return on their "
                "scope for eleven minutes"
            ),
            "category": "domain",
            "claim_type": "testimony",
            "provenance_chain": {
                "origin_kind": "named",
                "origin": "Group Captain Aled Furze",
                "relay": [],
            },
            "attribution_in_text": True,
            "attestation": "second_hand",
            "speaker": "Dr Helena Marsh",
            "location_in_record": "00:00:35",
            "date": "1994-03-15",
            "node_references": [
                {"name": "Coastal Air Defence Command", "role": "subject"},
                {"name": "Group Captain Aled Furze", "role": "participant"},
                {"name": "Skerrivore Point radar return", "role": "mentioned"},
            ],
            "confidence": 0.9,
        },
        {
            # second_hand by a relay of one, and the first of the record's two
            # distinct anonymous origins.
            "content": (
                "The Skerrivore Point radar tapes for the night of 14 March "
                "1994 were boxed and driven off the site before dawn."
            ),
            "original_excerpt": (
                "the tapes from that night were boxed and driven off the site "
                "before dawn"
            ),
            "category": "domain",
            "claim_type": "hearsay",
            "provenance_chain": {
                "origin_kind": "anonymous",
                "origin": "[a duty officer at Whitchurch Down]",
                "origin_ref": "duty-officer-1",
                "relay": [
                    "a retired radar technician from the Skerrivore Point station"
                ],
            },
            "attribution_in_text": True,
            "attestation": "second_hand",
            "speaker": "Dr Helena Marsh",
            "location_in_record": "line 4",
            "node_references": [
                {"name": "the boxed Skerrivore Point tapes", "role": "subject"},
                {"name": "Whitchurch Down", "role": "setting"},
                {"name": "Skerrivore Point", "role": "mentioned"},
            ],
            "confidence": 0.8,
        },
        {
            # third_hand: a document origin two removes from the speaker.
            "content": (
                "The boxed Skerrivore Point tapes were never entered in the "
                "receiving log at Whitchurch Down."
            ),
            "original_excerpt": (
                "the consignment was never entered in the receiving log at "
                "Whitchurch Down"
            ),
            "category": "domain",
            "claim_type": "administrative",
            "provenance_chain": {
                "origin_kind": "document",
                "origin": "Skerrivore Point incident file",
                "relay": ["a ministry archivist", "Group Captain Aled Furze"],
            },
            "attribution_in_text": True,
            "attestation": "third_hand",
            "speaker": "Dr Helena Marsh",
            "location_in_record": "00:01:00",
            "node_references": [
                {"name": "the boxed Skerrivore Point tapes", "role": "subject"},
                {"name": "Skerrivore Point incident file", "role": "mentioned"},
                {"name": "Whitchurch Down", "role": "setting"},
            ],
            "confidence": 0.7,
        },
        {
            # Two things at once, both deliberate. The declared attestation
            # contradicts the chain (named origin, empty relay, so second_hand),
            # and the speaker is a bracketed description, which the importer
            # must rewrite into an anonymous origin. This is the record's second
            # distinct anonymous origin_ref.
            "content": (
                "The boxed Skerrivore Point tapes were wiped at Whitchurch Down "
                "and returned to service."
            ),
            "original_excerpt": (
                "those same tapes were wiped at Whitchurch Down and put "
                "straight back into service"
            ),
            "category": "domain",
            "claim_type": "hearsay",
            "provenance_chain": {
                "origin_kind": "named",
                "origin": "[a colleague at Northern Reach Observatory]",
                "origin_ref": "colleague-1",
                "relay": [],
            },
            "attribution_in_text": True,
            "attestation": "first_hand",
            "speaker": "[a colleague at Northern Reach Observatory]",
            "location_in_record": "00:01:15",
            "node_references": [
                {"name": "the boxed Skerrivore Point tapes", "role": "subject"},
                {"name": "Whitchurch Down", "role": "setting"},
                {"name": "Northern Reach Observatory", "role": "mentioned"},
            ],
            "confidence": 0.6,
        },
        {
            # unattributed: the chain yields no attestation at all.
            "content": (
                "Eleven minutes of the Skerrivore Point radar return remain "
                "unexplained."
            ),
            "original_excerpt": "The eleven minutes are the part nobody has explained.",
            "category": "domain",
            "claim_type": "opinion",
            "provenance_chain": {
                "origin_kind": "unattributed",
                "origin": "",
                "relay": [],
            },
            "attribution_in_text": True,
            "speaker": None,
            "location_in_record": "end of the interview",
            "node_references": [
                {"name": "Skerrivore Point radar return", "role": "subject"},
            ],
            "confidence": 0.5,
        },
    ],
    "extraction_complete": True,
}

# --- Document B -------------------------------------------------------------

NODES_B = {
    "main_subject": "Skerrivore Point enquiry",
    "nodes": [
        {"name": "Dr Helena Marsh", "node_type": "person"},
        {"name": "Marion Kilbride", "node_type": "person"},
        {"name": "Northern Reach Observatory", "node_type": "organisation"},
        {
            "name": "Coastal Air Defence Command (CADC)",
            "node_type": "organisation",
        },
        {"name": "Skerrivore Point", "node_type": "place"},
    ],
    "codenames_to_resolve": [],
    "acronyms": [
        {"acronym": "CADC", "expansion": "Coastal Air Defence Command"},
    ],
    "extraction_complete": True,
}

CLAIMS_B = {
    "claims": [
        {
            "content": (
                "The Northern Reach Observatory plate register, duty log and "
                "seeing conditions sheet for 13 to 15 March 1994 are held in "
                "the director's cabinet."
            ),
            "original_excerpt": (
                "The plate register, the duty log and the seeing conditions "
                "sheet are all held in the director's cabinet"
            ),
            "category": "domain",
            "claim_type": "administrative",
            "provenance_chain": {
                "origin_kind": "speaker",
                "origin": "Marion Kilbride",
                "relay": [],
            },
            "attribution_in_text": False,
            "attestation": "first_hand",
            "speaker": "Marion Kilbride",
            "location_in_record": "file_page 1",
            "date": "1994-04-19",
            "node_references": [
                {"name": "Northern Reach Observatory", "role": "subject"},
                {"name": "Dr Helena Marsh", "role": "mentioned"},
            ],
            "confidence": 1.0,
        },
        {
            # DELIBERATELY NOT VERBATIM. The quote is a paraphrase, so the
            # aligner cannot place it and the model's own location string
            # survives - the unalignable branch, which a verbatim-only fixture
            # never reaches.
            "content": (
                "Coastal Air Defence Command has given no undertaking about "
                "the Skerrivore Point tapes."
            ),
            "original_excerpt": (
                "no undertaking whatsoever has been offered in respect of the tapes"
            ),
            "category": "domain",
            "claim_type": "administrative",
            "provenance_chain": {
                "origin_kind": "unattributed",
                "origin": "",
                "relay": [],
            },
            "attribution_in_text": False,
            "location_in_record": "file_page 1, paragraph 2",
            "node_references": [
                {"name": "Coastal Air Defence Command (CADC)", "role": "subject"},
                {"name": "Skerrivore Point", "role": "setting"},
            ],
            "confidence": 0.8,
        },
        {
            "content": (
                "The Skerrivore Point enquiry papers are filed under reference "
                "NRO/1994/17."
            ),
            "original_excerpt": (
                "The enquiry papers are filed under reference NRO/1994/17."
            ),
            "category": "infrastructure",
            "claim_type": "administrative",
            "provenance_chain": {
                "origin_kind": "speaker",
                "origin": "Marion Kilbride",
                "relay": [],
            },
            "attribution_in_text": False,
            "attestation": "first_hand",
            "speaker": "Marion Kilbride",
            "location_in_record": "file_page 1, paragraph 3",
            "node_references": [
                {"name": "Northern Reach Observatory", "role": "subject"},
                {"name": "Skerrivore Point", "role": "mentioned"},
            ],
            "confidence": 1.0,
        },
        {
            "content": (
                "Correspondence about the Skerrivore Point enquiry should be "
                "directed to Marion Kilbride."
            ),
            "original_excerpt": (
                "Correspondence on this matter should be directed to Marion Kilbride"
            ),
            "category": "infrastructure",
            "claim_type": "administrative",
            "provenance_chain": {
                "origin_kind": "speaker",
                "origin": "Marion Kilbride",
                "relay": [],
            },
            "attribution_in_text": True,
            "attestation": "first_hand",
            "speaker": "Marion Kilbride",
            "location_in_record": "file_page 1, paragraph 4",
            "node_references": [
                {"name": "Marion Kilbride", "role": "subject"},
                {"name": "Northern Reach Observatory", "role": "participant"},
            ],
            "confidence": 1.0,
        },
    ],
    "extraction_complete": True,
}


RESPONSES = {
    (DOCUMENT_A.key, "nodes"): NODES_A,
    (DOCUMENT_A.key, "claims"): CLAIMS_A,
    (DOCUMENT_B.key, "nodes"): NODES_B,
    (DOCUMENT_B.key, "claims"): CLAIMS_B,
}


# --- Resolution -------------------------------------------------------------


class UnexpectedModelCall(AssertionError):
    """A call these fixtures cannot answer.

    Raised rather than returning something plausible: an unanswerable call means
    the pipeline asked for something the fixtures do not describe, and a stub
    that improvises would hide it.
    """


def pass_of(schema: dict | None) -> str:
    """Which extraction pass built this schema.

    Read off the schema's shape rather than compared by identity: the claims
    pass builds a fresh dict per call from the node names, so there is no
    constant to compare against.
    """
    props = (schema or {}).get("properties") or {}
    if "nodes" in props:
        return "nodes"
    if "claims" in props:
        return "claims"
    raise UnexpectedModelCall(
        f"neither extraction pass built this schema: {sorted(props)}"
    )


def document_of(document: str) -> str:
    """Which fixture record this chunk came from."""
    matches = [d.key for d in (DOCUMENT_A, DOCUMENT_B) if d.marker in document]
    if len(matches) != 1:
        raise UnexpectedModelCall(
            "could not identify the fixture document from the chunk "
            f"(markers matched: {matches})"
        )
    return matches[0]


def response_for(
    preamble: str,
    document: str,
    task: str,
    model: str,
    schema: dict | None = None,
    use_api: bool = False,
) -> str:
    """Stand in for `digester.extract.call_with_document`.

    Returns the canned response as JSON text, which is what the transport
    returns and what both passes parse.
    """
    key = (document_of(document), pass_of(schema))
    if key not in RESPONSES:
        raise UnexpectedModelCall(f"no canned response for {key}")
    return json.dumps(RESPONSES[key])


# --- Validation against the live schemas ------------------------------------

_KNOWN_KEYWORDS = frozenset(
    {
        "type",
        "required",
        "properties",
        "items",
        "enum",
        "additionalProperties",
        "minimum",
        "maximum",
    }
)

_TYPE_CHECKS = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
    "null": type(None),
}


def _validate(value, schema: dict, path: str) -> None:
    """Check `value` against the subset of JSON Schema the live schemas use.

    Deliberately raises on a keyword it does not implement. A validator that
    ignores what it does not understand reports success for a constraint it
    never checked, which is the failure this exists to prevent.
    """
    unknown = set(schema) - _KNOWN_KEYWORDS
    if unknown:
        raise AssertionError(
            f"{path}: schema uses keywords this validator does not implement: "
            f"{sorted(unknown)} - extend it rather than skipping them"
        )

    declared = schema.get("type")
    if declared is not None:
        allowed = declared if isinstance(declared, list) else [declared]
        expected = tuple(_TYPE_CHECKS[t] for t in allowed)
        flat: tuple = tuple(
            t for e in expected for t in (e if isinstance(e, tuple) else (e,))
        )
        # bool is a subclass of int, so a boolean would satisfy "number".
        if isinstance(value, bool) and bool not in flat:
            raise AssertionError(f"{path}: boolean where {allowed} expected")
        if not isinstance(value, flat):
            raise AssertionError(
                f"{path}: {type(value).__name__} where {allowed} expected"
            )

    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError(f"{path}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise AssertionError(f"{path}: {value} below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise AssertionError(f"{path}: {value} above maximum {schema['maximum']}")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise AssertionError(f"{path}: required field {name!r} is missing")
        properties = schema.get("properties") or {}
        free = schema.get("additionalProperties") is True
        for name, item in value.items():
            if name in properties:
                _validate(item, properties[name], f"{path}.{name}")
            elif not free:
                # Stricter than the schema, on purpose: a key the prompt never
                # asks for is a fixture inventing a shape, whether or not the
                # schema happens to forbid it.
                raise AssertionError(
                    f"{path}: {name!r} is not a property the live schema declares"
                )

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{i}]")


def validate_responses() -> None:
    """Check every canned response against the schema the code builds at runtime."""
    from digester.extract import NODES_SCHEMA_V2, build_claims_schema_v2

    for doc_key in (DOCUMENT_A.key, DOCUMENT_B.key):
        nodes = RESPONSES[(doc_key, "nodes")]
        _validate(nodes, NODES_SCHEMA_V2, f"{doc_key}/nodes")
        node_names = [n["name"] for n in nodes["nodes"]]
        _validate(
            RESPONSES[(doc_key, "claims")],
            build_claims_schema_v2(node_names),
            f"{doc_key}/claims",
        )
