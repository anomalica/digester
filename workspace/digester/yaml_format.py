"""Digest interchange format - YAML.

Locked schema: anomalica/digest/1
See architecture/digest-format.md and decisions/0027-digest-interchange-format.md
in the meta-repo.

Field order at root: schema, extracted_at, model, record, nodes,
domain_claims, infrastructure_claims.
Per-node: id, type, name, metadata?
Per-claim: id, type, attestation, speaker?, location?, date|date_range?, refs?, quote?, text
Null/empty fields are omitted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import yaml

from digester.models import ExtractionResult


SCHEMA_VERSION = "anomalica/digest/1"


def _omit_empty(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _yaml_dump(doc: dict) -> str:
    def _repr_str(dumper, data: str):
        if "\n" in data or len(data) > 80:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    dumper = yaml.SafeDumper
    dumper.add_representer(str, _repr_str)
    return yaml.dump(
        doc,
        Dumper=dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_flow_style=False,
    )


def extraction_to_yaml(
    domain_result: ExtractionResult,
    infra_result: ExtractionResult | None = None,
    record_id: str | None = None,
    model: str = "unknown",
    terminology: dict | None = None,
) -> str:
    if record_id is None:
        record_id = str(uuid.uuid4())

    name_to_id: dict[str, str] = {}
    nodes_out: list[dict] = []
    seen: set[str] = set()

    def _add_node(node):
        if node.name in seen:
            return
        seen.add(node.name)
        nid = str(uuid.uuid4())
        name_to_id[node.name] = nid
        item = _omit_empty(
            {
                "id": nid,
                "type": node.node_type.value,
                "name": node.name,
                "metadata": node.metadata,
            }
        )
        nodes_out.append(item)

    for n in domain_result.nodes:
        _add_node(n)
    if infra_result:
        for n in infra_result.nodes:
            _add_node(n)

    def _ref(name: str) -> dict:
        rid = name_to_id.get(name)
        return {"id": rid, "name": name} if rid else {"name": name}

    def _emit_claim(claim) -> dict:
        item: dict = {
            "id": str(uuid.uuid4()),
            "type": claim.claim_type.value,
            "attestation": claim.attestation.value,
        }
        if claim.speaker:
            item["speaker"] = _ref(claim.speaker)
        if claim.location_in_record:
            item["location"] = claim.location_in_record
        if claim.date and claim.date_end:
            item["date_range"] = [claim.date, claim.date_end]
        elif claim.date:
            item["date"] = claim.date
        if claim.node_references:
            item["refs"] = [_ref(r) for r in claim.node_references]
        if claim.original_excerpt:
            item["quote"] = claim.original_excerpt
        if claim.content:
            item["text"] = claim.content
        return item

    domain_claims = [_emit_claim(c) for c in domain_result.claims]
    infra_claims = [_emit_claim(c) for c in infra_result.claims] if infra_result else []

    doc = _omit_empty(
        {
            "schema": SCHEMA_VERSION,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "record": _omit_empty(
                {
                    "id": record_id,
                    "title": domain_result.record_title,
                    "producer": domain_result.record_producer,
                    "date": domain_result.record_date,
                    "reference": domain_result.record_reference,
                }
            ),
            "terminology": terminology or None,
            "nodes": nodes_out,
            "domain_claims": domain_claims,
            "infrastructure_claims": infra_claims,
        }
    )

    return _yaml_dump(doc)


def two_pass_result_to_yaml(
    result: dict,
    record_title: str | None = None,
    record_producer: str | None = None,
    record_date: str | None = None,
    record_reference: str | None = None,
    record_id: str | None = None,
    model: str = "unknown",
) -> str:
    """Convert the dict returned by extract.extract_two_pass into a YAML
    digest. Splits claims into domain_claims and infrastructure_claims based
    on the per-claim category field so the output schema stays compatible
    with the existing assembler.
    """
    if record_id is None:
        record_id = str(uuid.uuid4())

    name_to_id: dict[str, str] = {}
    nodes_out: list[dict] = []
    for n in result.get("nodes", []):
        nid = str(uuid.uuid4())
        name_to_id[n["name"]] = nid
        item = _omit_empty(
            {
                "id": nid,
                "type": n.get("node_type") or n.get("type"),
                "name": n["name"],
                "metadata": n.get("metadata"),
            }
        )
        nodes_out.append(item)

    def _ref(name: str) -> dict:
        rid = name_to_id.get(name)
        return {"id": rid, "name": name} if rid else {"name": name}

    def _emit_claim(c: dict) -> dict:
        item: dict = {
            "id": str(uuid.uuid4()),
            "type": c["claim_type"],
        }
        if c.get("attestation"):
            item["attestation"] = c["attestation"]
        if c.get("speaker"):
            item["speaker"] = _ref(c["speaker"])
        if c.get("location_in_record"):
            item["location"] = c["location_in_record"]
        if c.get("date"):
            item["date"] = c["date"]
        if c.get("node_references"):
            item["refs"] = [_ref(r) for r in c["node_references"]]
        if c.get("original_excerpt"):
            item["quote"] = c["original_excerpt"]
        if c.get("content"):
            item["text"] = c["content"]
        return item

    domain_claims = []
    infra_claims = []
    for c in result.get("claims", []):
        emitted = _emit_claim(c)
        if c.get("category") == "infrastructure":
            infra_claims.append(emitted)
        else:
            domain_claims.append(emitted)

    terminology = {
        "main_subject": result.get("main_subject"),
        "codenames": result.get("codenames_to_resolve") or [],
        "acronyms": result.get("acronyms") or [],
    }
    terminology = _omit_empty(terminology) or None

    doc = _omit_empty(
        {
            "schema": SCHEMA_VERSION,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "record": _omit_empty(
                {
                    "id": record_id,
                    "title": record_title,
                    "producer": record_producer,
                    "date": record_date,
                    "reference": record_reference,
                }
            ),
            "terminology": terminology,
            "nodes": nodes_out,
            "domain_claims": domain_claims,
            "infrastructure_claims": infra_claims,
        }
    )
    return _yaml_dump(doc)


def parsed_dict_to_digest_yaml(parsed: dict) -> str:
    """Emit the locked YAML shape from a parsed-dict representation.

    Used by the legacy markdown -> YAML converter and by reclassify, both of
    which work on parsed-dict structures rather than ExtractionResult objects.
    Ids in the dict are preserved verbatim (no re-minting).
    """
    fm = parsed.get("frontmatter") or {}

    name_to_id: dict[str, str] = {}
    nodes_out: list[dict] = []
    for n in parsed.get("nodes", []):
        nid = n.get("id")
        name = n.get("name")
        if name and nid:
            name_to_id[name] = nid
        nodes_out.append(
            _omit_empty(
                {
                    "id": nid,
                    "type": n.get("node_type"),
                    "name": name,
                    "metadata": n.get("metadata"),
                }
            )
        )

    def _ref(name):
        rid = name_to_id.get(name)
        return {"id": rid, "name": name} if rid else {"name": name}

    def _claim(c: dict) -> dict:
        item: dict = {
            "id": c.get("id"),
            "type": c.get("claim_type"),
        }
        if c.get("attestation"):
            item["attestation"] = c["attestation"]
        if c.get("speaker"):
            item["speaker"] = _ref(c["speaker"])
        if c.get("location_in_record"):
            item["location"] = c["location_in_record"]
        if c.get("date") and c.get("date_end"):
            item["date_range"] = [c["date"], c["date_end"]]
        elif c.get("date"):
            item["date"] = c["date"]
        if c.get("node_references"):
            item["refs"] = [_ref(r) for r in c["node_references"]]
        if c.get("original_excerpt"):
            item["quote"] = c["original_excerpt"]
        if c.get("content"):
            item["text"] = c["content"]
        return item

    doc = _omit_empty(
        {
            "schema": SCHEMA_VERSION,
            "extracted_at": fm.get("extracted_at"),
            "model": fm.get("model"),
            "record": _omit_empty(
                {
                    "id": fm.get("record_id"),
                    "title": fm.get("record_title"),
                    "producer": fm.get("record_producer"),
                    "date": fm.get("record_date"),
                    "reference": fm.get("record_reference"),
                }
            ),
            "nodes": nodes_out,
            "domain_claims": [_claim(c) for c in parsed.get("domain_claims", [])],
            "infrastructure_claims": [
                _claim(c) for c in parsed.get("infrastructure_claims", [])
            ],
        }
    )
    return _yaml_dump(doc)


def parse_digest_yaml(text: str) -> dict:
    """Parse a .yaml digest into the same dict shape as parse_extraction_markdown.

    Keeps the downstream importer unchanged: it consumes node_references as a
    list of names. The richer {id, name} ref objects in the YAML are for the
    workbench and human readers, not the importer.
    """
    doc = yaml.safe_load(text) or {}
    record = doc.get("record") or {}
    frontmatter = {
        "record_id": record.get("id"),
        "record_title": record.get("title"),
        "record_producer": record.get("producer"),
        "record_date": record.get("date"),
        "record_reference": record.get("reference"),
        "extracted_at": doc.get("extracted_at"),
        "model": doc.get("model"),
        "schema": doc.get("schema"),
    }

    def _node(n: dict) -> dict:
        return {
            "id": n.get("id"),
            "node_type": n.get("type"),
            "name": n.get("name"),
            "metadata": n.get("metadata"),
        }

    def _ref_name(r):
        return r.get("name") if isinstance(r, dict) else r

    def _claim(c: dict) -> dict:
        spk = c.get("speaker")
        speaker = _ref_name(spk) if spk else None
        date = None
        date_end = None
        if "date_range" in c and isinstance(c["date_range"], list):
            dr = c["date_range"]
            if len(dr) >= 1:
                date = dr[0]
            if len(dr) >= 2:
                date_end = dr[1]
        elif "date" in c:
            date = c["date"]
        return {
            "id": c.get("id"),
            "claim_type": c.get("type"),
            "attestation": c.get("attestation"),
            "speaker": speaker,
            "content": c.get("text"),
            "original_excerpt": c.get("quote"),
            "node_references": [
                _ref_name(r) for r in (c.get("refs") or []) if _ref_name(r)
            ],
            "location_in_record": c.get("location"),
            "date": date,
            "date_end": date_end,
        }

    return {
        "frontmatter": frontmatter,
        "terminology": doc.get("terminology") or {},
        "nodes": [_node(n) for n in doc.get("nodes", [])],
        "domain_claims": [_claim(c) for c in doc.get("domain_claims", [])],
        "infrastructure_claims": [
            _claim(c) for c in doc.get("infrastructure_claims", [])
        ],
    }
