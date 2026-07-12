from __future__ import annotations

import json
import re
from pathlib import Path

import click

from anomalica_common.llm import (
    accumulate,
    estimate_batch,
    estimate_record,
    get_usage,
    get_usage_trace,
    reset_usage,
    resolve_use_api,
    spend_confirmed,
    usage_entry,
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
    "--digests-root",
    type=click.Path(),
    default=None,
    help="Write into a digests repo with the variant layout (ADR 0039): a "
    "model+prompt variant under variants/, and the canonical under records/ for "
    "a production run. Re-digests never overwrite prior ones.",
)
@click.option(
    "--variant-only",
    is_flag=True,
    help="With --digests-root, write only the model variant, never the canonical "
    "(a deliberate side-run with the active prompt; benchmarks).",
)
@click.option(
    "--predigests-root",
    type=click.Path(),
    default=None,
    help="Store the materialised pre-digest (ADR 0042) under this local, "
    "gitignored dir. Its hash is recorded in the digest regardless; this stores "
    "the artefact for the workbench's read-only pre-digest tab.",
)
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
    digests_root: str | None,
    variant_only: bool,
    predigests_root: str | None,
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

    _do_extract(
        path,
        parsed,
        Path(output) if output else None,
        model,
        use_api,
        Path(digests_root) if digests_root else None,
        variant_only,
        Path(predigests_root) if predigests_root else None,
    )


def _do_extract(
    path: Path,
    parsed,
    output: Path | None,
    model: str,
    use_api: bool = False,
    digests_root: Path | None = None,
    variant_only: bool = False,
    predigests_root: Path | None = None,
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
    from anomalica_common.pre_digest import (
        PREP_VERSION,
        materialise,
        pre_digest_hash,
        store_pre_digest,
    )
    from digester.extract import build_record_context, extract_two_pass

    record_context = build_record_context(
        title=parsed.title,
        creators=parsed.creators,
        date=parsed.date,
        source_type=parsed.source_type,
    )

    # Pre-digest (ADR 0042): the materialised model input. Its hash is recorded in
    # every digest for exact reproducibility; the artefact is stored (gitignored,
    # copyright-bearing) only when a predigests-root is configured.
    pre_digest_text = materialise(parsed.body)
    pd_sha = pre_digest_hash(pre_digest_text)
    if predigests_root is not None:
        record_key = (parsed.metadata.get("content_hash") or path.stem).removeprefix(
            "sha256:"
        )
        store_pre_digest(predigests_root, record_key, pre_digest_text)

    click.echo(f"Extracting (two-pass) from: {parsed.title or path.name}")
    reset_usage()
    try:
        result = extract_two_pass(
            parsed.body,
            model=model,
            record_context=record_context,
            on_progress=click.echo,
            use_api=use_api,
        )

        # Public AI-usage provenance (ADR 0037 inline emission): this digest's
        # extract entry, carried forward onto any upstream chain the ingest
        # record already published (record -> digest -> article).
        upstream = parsed.metadata.get("ai_usage")
        ai_usage = accumulate(
            upstream if isinstance(upstream, list) else None,
            usage_entry("digest", model, get_usage()),
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
            record_reference=parsed.reference,
            record_processing_version=(parsed.metadata.get("processing") or {}).get(
                "version"
            ),
            model=model,
            ai_usage=ai_usage,
            pre_digest={"sha256": pd_sha, "prep_version": PREP_VERSION},
        )

        if digests_root is not None:
            from digester import digest_store

            written = digest_store.write_digest(
                digests_root,
                path.stem,
                text,
                model,
                result.get("prompt_provenance"),
                variant_only=variant_only,
            )
            click.echo(f"\nVariant: {written['variant']}")
            if written["canonical"]:
                click.echo(f"Canonical (latest-written): {written['canonical']}")
            else:
                click.echo(
                    "Canonical: unchanged (prompt override / experimental, or --variant-only)"
                )
            return written["variant"]

        out_path = output if output else path.with_suffix(".yaml")
        out_path.write_text(text)
        click.echo(f"\nWritten to: {out_path}")
        return out_path
    finally:
        _echo_usage()


def _echo_usage() -> None:
    """Emit per-job token usage for a runner to capture (telemetry lives in the
    caller's store, not the digests repo). Printed on BOTH the success and
    failure paths so a failed/crashed job's spent tokens still register on the
    caller's usage trend - otherwise a crash-loop burns the rate-limit budget
    invisibly. Success detection stays exit-code + output-file-exists,
    independent of this line. USAGE_JSON is the machine line; cost_equiv_usd is
    the would-be dollar value (zero actual dollars on the subscription default).
    """
    usage = get_usage()
    click.echo(
        f"Usage: {usage['calls']} calls, "
        f"in={usage['input_tokens']} out={usage['output_tokens']} "
        f"cache_read={usage['cache_read_input_tokens']} "
        f"cost_equiv=${usage['cost_equiv_usd']:.4f}"
    )
    click.echo(f"USAGE_JSON: {json.dumps(usage)}")
    click.echo(f"TRACE_JSON: {json.dumps(get_usage_trace())}")


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
    "--digests-root",
    type=click.Path(),
    default=None,
    help="Write into a digests repo with the variant layout (ADR 0039): "
    "per-record model+prompt variants under variants/, canonical under records/. "
    "Re-digests never overwrite prior ones.",
)
@click.option(
    "--variant-only",
    is_flag=True,
    help="With --digests-root, write only variants, never the canonical.",
)
@click.option(
    "--predigests-root",
    type=click.Path(),
    default=None,
    help="Store each record's materialised pre-digest (ADR 0042) under this "
    "local, gitignored dir for the workbench's pre-digest tab.",
)
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
    digests_root: str | None,
    variant_only: bool,
    predigests_root: str | None,
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

    root = Path(digests_root) if digests_root else None
    pd_root = Path(predigests_root) if predigests_root else None
    out_dir = Path(output_dir) if output_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for i, (p, parsed) in enumerate(parsed_records, 1):
        click.echo(f"\n[{i}/{len(parsed_records)}] {p.name}")
        out = out_dir / p.with_suffix(".yaml").name if out_dir else None
        _do_extract(p, parsed, out, model, use_api, root, variant_only, pd_root)


# --- Normalise locations: put every variant on one canonical time axis ---


@main.command(name="normalise-locations")
@click.argument("digest_path", type=click.Path(exists=True))
@click.option(
    "--record",
    "record_path",
    required=True,
    type=click.Path(exists=True),
    help="The source record the digest was extracted from (supplies word timing)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write here instead of rewriting the digest in place",
)
def normalise_locations_cmd(
    digest_path: str, record_path: str, output: str | None
) -> None:
    """Rewrite every claim's location to a canonical HH:MM:SS.d range.

    Deterministic post-process, no model call and no spend: each claim's quote is
    verbatim, so its span is recovered by aligning it to the record's word stream.
    Variants extracted by different models otherwise write locations on whatever
    axis each chose - bare seconds, timecodes, even source line numbers - and
    cannot be clustered against one another.
    """
    import yaml

    from digester.realign import normalise_claim_locations, words_from_record2

    digest = yaml.safe_load(Path(digest_path).read_text())
    parsed = parse_record(Path(record_path).read_text())
    words, times = words_from_record2(parsed.body)
    if not words:
        raise click.ClickException(
            "No word timing in the record - normalisation needs a record/2 body "
            "with inline {{t:}} tokens."
        )

    claims = (digest.get("domain_claims") or []) + (
        digest.get("infrastructure_claims") or []
    )
    stats = normalise_claim_locations(claims, words, times)

    out = Path(output) if output else Path(digest_path)
    out.write_text(yaml.safe_dump(digest, sort_keys=False, allow_unicode=True))

    click.echo(
        f"Aligned {stats['aligned']}/{stats['total']} claims "
        f"({stats['unaligned']} unalignable, {stats['ambiguous']} ambiguous)"
    )
    click.echo(f"Written to: {out}")


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
