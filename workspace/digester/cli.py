from __future__ import annotations

import re
from pathlib import Path

import click

from anomalica_common.llm import (
    estimate_batch,
    estimate_record,
    resolve_use_api,
    spend_confirmed,
)
from digester.record_parser import parse_record

# The digester resolves its own metered toggle: DIGESTER_USE_API > global
# ANOMALICA_USE_API > subscription (per-component scheme; see anomalica/CLAUDE.md).
_USE_API_VAR = "DIGESTER_USE_API"

# Annotation tokens (e.g. {{redacted}}) can leak into a record's creators list
# from ingest extraction; they must never surface as the producer.
_ANNOTATION_TOKEN = re.compile(r"^\s*\{\{.*\}\}\s*$")


def _producer_from_creators(creators: list[str] | None) -> str | None:
    """The producer is the first creator that is a real name, skipping
    annotation tokens like {{redacted}} (defensive - the ingester also stops
    emitting them and reviewers can edit creators)."""
    for c in creators or []:
        if c and not _ANNOTATION_TOKEN.match(c):
            return c
    return None


@click.group()
def main() -> None:
    """Anomalica digester - per-record extraction (record -> digest file).

    The graph-building half (import, dedup, scoring, corroboration, search,
    export) moved to the assimilator. This tool produces per-record digest files
    and reports review coverage; it no longer owns the knowledge graph.
    """


# --- Extract: AI produces a per-record digest ---


@main.command(name="extract")
@click.argument("file_path", type=click.Path(exists=True))
@click.option(
    "--output", "-o", type=click.Path(), default=None, help="Output digest YAML path"
)
@click.option("--model", default="sonnet", help="Claude model to use")
@click.option(
    "--confirm",
    is_flag=True,
    help="Confirm the printed cost estimate and proceed with the metered run "
    "(required for any spend; see anomalica/CLAUDE.md spend gate)",
)
@click.pass_context
def extract_cmd(
    ctx: click.Context,
    file_path: str,
    output: str | None,
    model: str,
    confirm: bool,
) -> None:
    """Extract knowledge from a record into a reviewable digest YAML file."""
    path = Path(file_path)
    text = path.read_text()

    click.echo(f"Parsing record: {path.name}")
    parsed = parse_record(text)

    # SPEND GATE (anomalica/CLAUDE.md operating rule): when this run will hit
    # the metered API, print a cost estimate and refuse to proceed without an
    # explicit --confirm. A promise/convention is not enough - this is the gate.
    use_api = resolve_use_api(_USE_API_VAR)
    if not spend_confirmed(
        estimate_record(len(parsed.body or ""), model),
        model,
        confirm,
        echo=click.echo,
        use_api=use_api,
    ):
        ctx.exit(2)

    _do_extract(path, parsed, Path(output) if output else None, model, use_api)


def _do_extract(
    path: Path, parsed, output: Path | None, model: str, use_api: bool = False
) -> Path:
    """Run the two-pass extraction for one parsed record and write the digest YAML.

    Caller is responsible for the spend gate - this assumes the run is approved.
    Returns the path the digest was written to.
    """
    # 2026-05-25 architecture: two-pass extract. Pass A nodes-only (with
    # chunking + iteration + cross-chunk directory threading + main_subject /
    # codenames / acronyms folded in from the deprecated terminology pre-pass).
    # Pass B claims-only, constrained to using only Pass A's node names via
    # JSON schema enum on node_references items. Each claim carries
    # category=domain|infrastructure for the assembler to filter on.
    from anomalica_common.digest import two_pass_result_to_yaml
    from digester.extract import build_record_context, extract_two_pass

    record_context = build_record_context(
        title=parsed.title,
        creators=parsed.creators,
        date=parsed.date,
        source_type=parsed.source_type,
    )

    click.echo(f"Extracting (two-pass) from: {parsed.title or path.name}")
    result = extract_two_pass(
        parsed.body,
        model=model,
        record_context=record_context,
        on_progress=click.echo,
        use_api=use_api,
    )

    text = two_pass_result_to_yaml(
        result,
        record_title=parsed.title,
        record_producer=_producer_from_creators(parsed.creators),
        record_publisher=parsed.metadata.get("publisher"),
        record_date=parsed.date,
        record_medium=parsed.source_type,
        record_duration=parsed.metadata.get("duration"),
        record_content_hash=parsed.metadata.get("content_hash"),
        model=model,
    )

    out_path = output if output else path.with_suffix(".yaml")
    out_path.write_text(text)
    click.echo(f"\nWritten to: {out_path}")
    return out_path


@main.command(name="batch-extract")
@click.argument("file_paths", nargs=-1, type=click.Path(exists=True), required=True)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(),
    default=None,
    help="Directory for the digest YAML files (default: alongside each record)",
)
@click.option("--model", default="sonnet", help="Claude model to use")
@click.option(
    "--confirm",
    is_flag=True,
    help="Confirm the printed aggregate cost estimate and proceed with the "
    "metered run (required for any spend; see anomalica/CLAUDE.md spend gate)",
)
@click.pass_context
def batch_extract_cmd(
    ctx: click.Context,
    file_paths: tuple[str, ...],
    output_dir: str | None,
    model: str,
    confirm: bool,
) -> None:
    """Extract knowledge from many records, behind one aggregate spend gate.

    Prints a single cost estimate for the whole batch and refuses to run
    without --confirm. This is the corpus-scale path - the one the spend rule
    exists to guard.
    """
    paths = [Path(p) for p in file_paths]
    parsed_records = []
    for p in paths:
        click.echo(f"Parsing record: {p.name}")
        parsed_records.append((p, parse_record(p.read_text())))

    # SPEND GATE: one aggregate estimate for the whole batch.
    char_counts = [len(parsed.body or "") for _, parsed in parsed_records]
    use_api = resolve_use_api(_USE_API_VAR)
    if not spend_confirmed(
        estimate_batch(char_counts, model),
        model,
        confirm,
        echo=click.echo,
        use_api=use_api,
    ):
        ctx.exit(2)

    out_dir = Path(output_dir) if output_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for i, (p, parsed) in enumerate(parsed_records, 1):
        click.echo(f"\n[{i}/{len(parsed_records)}] {p.name}")
        out = out_dir / p.with_suffix(".yaml").name if out_dir else None
        _do_extract(p, parsed, out, model, use_api)


# --- Coverage: review-gate visibility (which records are digestible) ---


@main.command(name="coverage")
@click.argument("records_dir", type=click.Path(exists=True))
@click.option(
    "--threshold",
    default=1.0,
    type=float,
    help="Observed-coverage fraction required to be digestible (default 1.0)",
)
def coverage_cmd(records_dir: str, threshold: float) -> None:
    """Report each record's review observation coverage and digestibility.

    The review-gate (quality, not cost): a record is digestible only when a
    reviewer has observed all of it. Records with no review sidecar are
    unreviewed and not digestible. Sorted by coverage so the review backlog is
    visible at a glance.
    """
    from digester.review_gate import assess_record

    rdir = Path(records_dir)
    rows = []
    for md in sorted(rdir.glob("*.md")):
        d = assess_record(md, rdir.parent, threshold)
        rows.append((md.name, d))
    rows.sort(key=lambda r: r[1].observed_coverage, reverse=True)

    digestible = sum(1 for _, d in rows if d.digestible)
    click.echo(
        f"{digestible}/{len(rows)} records digestible (observed >= {threshold:.0%})\n"
    )
    click.echo(f"{'cov':>6}  {'digest':>6}  record")
    for name, d in rows:
        flag = "YES" if d.digestible else "no"
        click.echo(f"{d.observed_coverage:>6.1%}  {flag:>6}  {name[:72]}")


if __name__ == "__main__":
    main()
