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
)
from digester.embeddings import (
    embed_batch,
    embed_text,
    init_vec,
    store_claim_embedding,
    store_node_embedding,
)
from digester.extract import extract, extract_infrastructure
from digester.import_markdown import import_extraction
from digester.markdown_format import extraction_to_markdown, parse_extraction_markdown
from digester.record_parser import parse_record
from digester.scoring import score_claim, tier_label

DEFAULT_DB = Path.home() / ".local" / "share" / "digester" / "knowledge.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    return conn


def _build_node_directory(*connections: sqlite3.Connection) -> list[tuple[str, str]]:
    seen = set()
    directory = []
    for conn in connections:
        for n in get_nodes(conn):
            if n.name not in seen:
                seen.add(n.name)
                directory.append((n.name, n.node_type.value))
    return directory


@click.group()
@click.option("--db", type=click.Path(), default=str(DEFAULT_DB), envvar="DIGESTER_DB")
@click.pass_context
def main(ctx: click.Context, db: str) -> None:
    """Anomalica digester - knowledge graph extraction engine."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = Path(db)
    ctx.obj["infra_db_path"] = Path(db).parent / "infrastructure.db"


# --- Extract: AI produces markdown ---


@main.command(name="extract")
@click.argument("file_path", type=click.Path(exists=True))
@click.option(
    "--output", "-o", type=click.Path(), default=None, help="Output markdown path"
)
@click.option("--model", default="sonnet", help="Claude model to use")
@click.option("--api", is_flag=True, help="Use Anthropic API instead of CLI")
@click.option("--domain-only", is_flag=True, help="Skip infrastructure extraction")
@click.pass_context
def extract_cmd(
    ctx: click.Context,
    file_path: str,
    output: str | None,
    model: str,
    api: bool,
    domain_only: bool,
) -> None:
    """Extract knowledge from a record into a reviewable markdown file."""
    path = Path(file_path)
    text = path.read_text()

    click.echo(f"Parsing record: {path.name}")
    parsed = parse_record(text)

    # Build node directory from existing databases
    domain_conn = _connect(ctx.obj["db_path"])
    infra_conn = _connect(ctx.obj["infra_db_path"])
    existing_nodes = _build_node_directory(domain_conn, infra_conn)
    if existing_nodes:
        click.echo(f"  Node directory: {len(existing_nodes)} existing nodes")
    domain_conn.close()
    infra_conn.close()

    # Domain extraction
    click.echo(f"Extracting domain knowledge from: {parsed.title or path.name}")
    domain_result = extract(
        parsed.body, model=model, use_api=api, existing_nodes=existing_nodes or None
    )
    click.echo(
        f"  {len(domain_result.nodes)} nodes, {len(domain_result.claims)} domain claims"
    )

    # Infrastructure extraction
    infra_result = None
    if not domain_only:
        click.echo("Extracting infrastructure...")
        infra_result = extract_infrastructure(
            parsed.body, model=model, use_api=api, existing_nodes=existing_nodes or None
        )
        click.echo(f"  {len(infra_result.claims)} infrastructure claims")

    # Write markdown
    md = extraction_to_markdown(domain_result, infra_result=infra_result, model=model)

    if output:
        out_path = Path(output)
    else:
        out_path = path.with_suffix(".extract.md")

    out_path.write_text(md)
    click.echo(f"\nWritten to: {out_path}")


# --- Import: deterministic markdown to database ---


@main.command(name="import")
@click.argument("file_path", type=click.Path(exists=True))
@click.pass_context
def import_cmd(ctx: click.Context, file_path: str) -> None:
    """Import a reviewed extraction markdown into the database. No AI involved."""
    path = Path(file_path)
    text = path.read_text()

    click.echo(f"Parsing extraction: {path.name}")
    parsed = parse_extraction_markdown(text)

    domain_conn = _connect(ctx.obj["db_path"])
    infra_conn = _connect(ctx.obj["infra_db_path"])

    # Import domain claims
    if parsed["domain_claims"]:
        click.echo("Importing domain claims...")
        counts = import_extraction(
            domain_conn,
            parsed,
            section="domain",
            lookup_conns=[infra_conn],
            on_progress=click.echo,
        )
        click.echo(
            f"  Domain: {counts['nodes_created']} new nodes, "
            f"{counts['nodes_matched']} matched, "
            f"{counts['claims_created']} claims"
        )

    # Import infrastructure claims
    if parsed["infrastructure_claims"]:
        click.echo("Importing infrastructure claims...")
        counts = import_extraction(
            infra_conn,
            parsed,
            section="infrastructure",
            lookup_conns=[domain_conn],
            on_progress=click.echo,
        )
        click.echo(
            f"  Infrastructure: {counts['nodes_created']} new nodes, "
            f"{counts['nodes_matched']} matched, "
            f"{counts['claims_created']} claims"
        )

    domain_conn.close()
    infra_conn.close()


@main.command()
@click.argument("directory", type=click.Path(exists=True))
@click.pass_context
def rebuild(ctx: click.Context, directory: str) -> None:
    """Rebuild the database from a directory of extraction markdown files.

    Deletes and recreates both domain and infrastructure databases,
    then imports all .extract.md files from the given directory.
    """
    import os

    db_path = ctx.obj["db_path"]
    infra_path = ctx.obj["infra_db_path"]

    # Delete existing databases
    for p in [db_path, infra_path]:
        if p.exists():
            os.remove(p)
            click.echo(f"Deleted {p}")

    # Find all extraction files
    directory_path = Path(directory)
    files = sorted(directory_path.glob("**/*.extract.md"))
    if not files:
        click.echo(f"No .extract.md files found in {directory}")
        return

    click.echo(f"Found {len(files)} extraction files in {directory}")

    # Import each file sequentially
    for i, f in enumerate(files, 1):
        click.echo(f"\n[{i}/{len(files)}] {f.name}")
        ctx.invoke(import_cmd, file_path=str(f))

    # Show final stats
    domain_conn = _connect(db_path)
    s = get_stats(domain_conn)
    click.echo(
        f"\nRebuild complete. Domain: {s['active_nodes']} nodes, "
        f"{s['records']} records, {s['claims']} claims."
    )
    domain_conn.close()


# --- Digest: extract then import (convenience) ---


@main.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option(
    "--output", "-o", type=click.Path(), default=None, help="Output markdown path"
)
@click.option("--model", default="sonnet", help="Claude model to use")
@click.option("--api", is_flag=True, help="Use Anthropic API instead of CLI")
@click.option("--domain-only", is_flag=True, help="Skip infrastructure extraction")
@click.pass_context
def digest(
    ctx: click.Context,
    file_path: str,
    output: str | None,
    model: str,
    api: bool,
    domain_only: bool,
) -> None:
    """Digest a record: extract to markdown then import into database."""
    ctx.invoke(
        extract_cmd,
        file_path=file_path,
        output=output,
        model=model,
        api=api,
        domain_only=domain_only,
    )

    # Determine the markdown path
    path = Path(file_path)
    md_path = Path(output) if output else path.with_suffix(".extract.md")

    ctx.invoke(import_cmd, file_path=str(md_path))


# --- Query commands ---


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
    click.echo(f"Corroborations: {s['corroborations']}")
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
            corr_str = ""
            if breakdown.corroboration_count > 0:
                corr_str = f", {breakdown.record_count} sources"
            click.echo(
                f"  [{c.claim_type.value}/{c.attestation.value}] "
                f"({label}, {breakdown.score:.2f}{corr_str}) {c.content}"
            )
    conn.close()


@main.command()
@click.pass_context
def embed(ctx: click.Context) -> None:
    """Embed all claims and nodes for similarity search."""
    conn = _connect(ctx.obj["db_path"])
    init_vec(conn)

    rows = conn.execute("SELECT id, content FROM claims").fetchall()
    if rows:
        click.echo(f"Embedding {len(rows)} claims...")
        ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]
        embeddings = embed_batch(texts)
        for claim_id, emb in zip(ids, embeddings):
            store_claim_embedding(conn, claim_id, emb)
        click.echo(f"  Stored {len(rows)} claim embeddings.")

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


CORROBORATION_VERIFY_PROMPT = """Below are pairs of claims from different records. For each pair, decide whether they assert the SAME underlying fact or are genuinely DIFFERENT assertions.

RULES:
- "same": the claims make the same factual assertion, possibly with different wording or detail level.
- "different": the claims are about different things, even if they are thematically related.
- Two claims about the same TOPIC but making different ASSERTIONS are "different".
  Example: "The object was 12 metres long" and "The object had no wings" are both about the object, but different assertions.
- Two claims making the same ASSERTION in different words are "same".
  Example: "The object traversed 100km in seconds" and "The UAP covered approximately 100 kilometres almost instantly" are the same.

{pairs_text}

OUTPUT FORMAT (respond with ONLY valid JSON, no markdown fencing):

{{"decisions": [
    {{"pair_id": 1, "verdict": "same"}},
    {{"pair_id": 2, "verdict": "different"}}
]}}"""


@main.command()
@click.option(
    "--threshold",
    default=0.99,
    help="Minimum embedding similarity to consider as candidate",
)
@click.option("--model", default="sonnet", help="Claude model for verification")
@click.pass_context
def corroborate(ctx: click.Context, threshold: float, model: str) -> None:
    """Find cross-record corroborations: embedding similarity then AI verification."""
    from digester.database import insert_corroboration
    from digester.embeddings import deserialise_f32, search_similar_claims
    from digester.extract import _call_cli, _parse_json

    conn = _connect(ctx.obj["db_path"])
    init_vec(conn)

    claims = conn.execute("SELECT id, record_id, content FROM claims").fetchall()
    claim_records = {cid: rid for cid, rid, _ in claims}
    claim_content = {cid: content for cid, _, content in claims}

    candidates = []
    seen_pairs = set()
    for claim_id, record_id, _ in claims:
        emb_row = conn.execute(
            "SELECT embedding FROM vec_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if not emb_row:
            continue
        vec = deserialise_f32(emb_row[0])
        matches = search_similar_claims(conn, vec, limit=5)
        for match_id, distance in matches:
            if match_id == claim_id:
                continue
            if claim_records.get(match_id) == record_id:
                continue
            pair_key = tuple(sorted([claim_id, match_id]))
            if pair_key in seen_pairs:
                continue
            similarity = 1.0 - distance
            if similarity >= threshold:
                seen_pairs.add(pair_key)
                candidates.append((claim_id, match_id, similarity))

    click.echo(f"Found {len(candidates)} candidate pairs above {threshold} similarity")

    if not candidates:
        conn.close()
        return

    batch_size = 20
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start : batch_start + batch_size]
        lines = []
        for i, (cid_a, cid_b, sim) in enumerate(batch, 1):
            lines.append(f"PAIR {i} (similarity: {sim:.3f}):")
            lines.append(f'  A: "{claim_content[cid_a]}"')
            lines.append(f'  B: "{claim_content[cid_b]}"')
            lines.append("")

        prompt = CORROBORATION_VERIFY_PROMPT.format(pairs_text="\n".join(lines))
        click.echo(f"  Verifying pairs {batch_start + 1}-{batch_start + len(batch)}...")
        raw = _call_cli(prompt, "", model)
        data = _parse_json(raw)
        decisions = {
            d.get("pair_id"): d.get("verdict") for d in data.get("decisions", [])
        }

        for i, (cid_a, cid_b, sim) in enumerate(batch, 1):
            verdict = decisions.get(i, "different")
            if verdict == "same":
                insert_corroboration(conn, cid_a, cid_b, sim)

    conn.commit()
    actual = conn.execute("SELECT COUNT(*) FROM corroborations").fetchone()[0]
    click.echo(
        f"Verified {actual} genuine corroborations (from {len(candidates)} candidates)"
    )
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
