from __future__ import annotations

import sqlite3
from pathlib import Path

import click

from digester.database import (
    get_claims_for_node,
    get_nodes,
    get_stats,
    find_node_by_name,
    init_db,
    insert_claim,
    insert_node,
    insert_record,
)
from digester.embeddings import embed_text, init_vec, store_node_embedding
from digester.extract import extract
from digester.models import Claim, Node, Record
from digester.record_parser import parse_record
from digester.scoring import score_claim, tier_label

DEFAULT_DB = Path.home() / ".local" / "share" / "digester" / "knowledge.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    return conn


@click.group()
@click.option("--db", type=click.Path(), default=str(DEFAULT_DB), envvar="DIGESTER_DB")
@click.pass_context
def main(ctx: click.Context, db: str) -> None:
    """Anomalica digester - knowledge graph extraction engine."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = Path(db)


@main.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--model", default="sonnet", help="Claude model to use for extraction")
@click.option("--api", is_flag=True, help="Use Anthropic API instead of CLI")
@click.pass_context
def digest(ctx: click.Context, file_path: str, model: str, api: bool) -> None:
    """Digest a record file into the knowledge graph."""
    conn = _connect(ctx.obj["db_path"])
    path = Path(file_path)
    text = path.read_text()

    click.echo(f"Parsing record: {path.name}")
    parsed = parse_record(text)

    # Build node directory from existing graph
    existing_nodes = []
    all_nodes = get_nodes(conn)
    if all_nodes:
        existing_nodes = [(n.name, n.node_type.value) for n in all_nodes]
        click.echo(f"  Node directory: {len(existing_nodes)} existing nodes")

    click.echo(f"Extracting from: {parsed.title or path.name}")
    result = extract(
        parsed.body, model=model, use_api=api, existing_nodes=existing_nodes or None
    )

    # Create the record node
    record = insert_record(
        conn,
        Record(
            title=result.record_title or parsed.title or path.name,
            reference=parsed.reference,
            date=result.record_date or parsed.date,
        ),
    )
    click.echo(f"  Record: {record.title} [{record.id[:8]}]")

    # Create or find domain nodes
    # Node deduplication relies on the extraction prompt receiving the existing
    # node directory so Claude uses canonical names. Exact name and alias matching
    # handles the rest. Embedding similarity is not used for node matching because
    # short names in the same domain cluster too tightly to distinguish.
    node_map: dict[str, str] = {}  # name -> node_id
    for extracted in result.nodes:
        existing = find_node_by_name(conn, extracted.name, extracted.node_type.value)
        if existing:
            node_map[extracted.name] = existing.id
            click.echo(
                f"  Existing node: {extracted.name} ({extracted.node_type.value}) [{existing.id[:8]}]"
            )
        else:
            node = insert_node(
                conn,
                Node(
                    node_type=extracted.node_type,
                    name=extracted.name,
                    metadata=extracted.metadata,
                ),
            )
            node_map[extracted.name] = node.id
            click.echo(
                f"  New node: {extracted.name} ({extracted.node_type.value}) [{node.id[:8]}]"
            )

    # If the record has a producer, link it
    if result.record_producer:
        producer = find_node_by_name(conn, result.record_producer)
        if producer:
            record.model_copy(update={"producer_id": producer.id})
            conn.execute(
                "UPDATE records SET producer_id = ? WHERE id = ?",
                (producer.id, record.id),
            )
        else:
            click.echo(
                f"  Warning: producer '{result.record_producer}' not found in nodes"
            )

    # Create claims
    for extracted_claim in result.claims:
        # Resolve node references to IDs
        ref_ids = []
        for ref_name in extracted_claim.node_references:
            if ref_name in node_map:
                ref_ids.append(node_map[ref_name])

        # Resolve speaker
        speaker_id = None
        if extracted_claim.speaker and extracted_claim.speaker in node_map:
            speaker_id = node_map[extracted_claim.speaker]

        insert_claim(
            conn,
            Claim(
                content=extracted_claim.content,
                claim_type=extracted_claim.claim_type,
                attestation=extracted_claim.attestation,
                record_id=record.id,
                speaker_id=speaker_id,
                location_in_record=extracted_claim.location_in_record,
                date=extracted_claim.date,
                date_end=extracted_claim.date_end,
                node_references=ref_ids,
                confidence=extracted_claim.confidence,
            ),
        )

    conn.commit()

    stats = get_stats(conn)
    click.echo(
        f"\nDone. Graph now has {stats['active_nodes']} nodes, "
        f"{stats['records']} records, {stats['claims']} claims."
    )
    conn.close()


@main.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show knowledge graph statistics."""
    conn = _connect(ctx.obj["db_path"])
    s = get_stats(conn)
    click.echo(f"Nodes: {s['active_nodes']} (total: {s['nodes']})")
    click.echo(f"Records: {s['records']}")
    click.echo(f"Claims: {s['claims']}")
    click.echo(f"Claim-node references: {s['claim_node_refs']}")
    click.echo(f"Aliases: {s['aliases']}")
    if s.get("by_type"):
        click.echo("\nBy type:")
        for node_type, count in sorted(s["by_type"].items()):
            click.echo(f"  {node_type}: {count}")
    conn.close()


@main.command()
@click.argument("name")
@click.pass_context
def show(ctx: click.Context, name: str) -> None:
    """Show a node and its claims."""
    conn = _connect(ctx.obj["db_path"])
    node = find_node_by_name(conn, name)
    if node is None:
        click.echo(f"Node not found: {name}")
        return
    click.echo(f"{node.node_type.value}: {node.name} [{node.id[:8]}]")
    if node.metadata:
        for k, v in node.metadata.items():
            click.echo(f"  {k}: {v}")
    claims = get_claims_for_node(conn, node.id)
    if claims:
        click.echo(f"\n{len(claims)} claim(s):")
        for c in claims:
            breakdown = score_claim(conn, c.id)
            label = tier_label(breakdown.score)
            click.echo(
                f"  [{c.claim_type.value}/{c.attestation.value}] "
                f"({label}, {breakdown.score:.2f}) {c.content}"
            )
    conn.close()


@main.command()
@click.pass_context
def embed(ctx: click.Context) -> None:
    """Embed all claims and nodes for similarity search."""
    from digester.embeddings import (
        embed_batch,
        store_claim_embedding,
    )

    conn = _connect(ctx.obj["db_path"])
    init_vec(conn)

    # Embed claims
    rows = conn.execute("SELECT id, content FROM claims").fetchall()
    if rows:
        click.echo(f"Embedding {len(rows)} claims...")
        ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]
        embeddings = embed_batch(texts)
        for claim_id, emb in zip(ids, embeddings):
            store_claim_embedding(conn, claim_id, emb)
        click.echo(f"  Stored {len(rows)} claim embeddings.")

    # Embed nodes
    node_rows = conn.execute(
        "SELECT id, name FROM nodes WHERE retired_at IS NULL"
    ).fetchall()
    if node_rows:
        click.echo(f"Embedding {len(node_rows)} nodes...")
        ids = [r[0] for r in node_rows]
        texts = [r[1] for r in node_rows]
        embeddings = embed_batch(texts)
        for node_id, emb in zip(ids, embeddings):
            store_node_embedding(conn, node_id, emb)
        click.echo(f"  Stored {len(node_rows)} node embeddings.")

    conn.commit()
    conn.close()


@main.command()
@click.argument("query")
@click.option("--limit", default=5, help="Number of results")
@click.pass_context
def search(ctx: click.Context, query: str, limit: int) -> None:
    """Search claims by semantic similarity."""
    from digester.embeddings import search_similar_claims

    conn = _connect(ctx.obj["db_path"])
    init_vec(conn)

    query_embedding = embed_text(query)
    results = search_similar_claims(conn, query_embedding, limit=limit)

    if not results:
        click.echo("No results. Run 'embed' first to generate embeddings.")
        conn.close()
        return

    for claim_id, distance in results:
        similarity = 1.0 - distance
        row = conn.execute(
            "SELECT content, claim_type FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        if row:
            click.echo(f"  [{similarity:.2f}] ({row[1]}) {row[0]}")
    conn.close()


if __name__ == "__main__":
    main()
