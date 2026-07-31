from __future__ import annotations

import json
import re
from pathlib import Path

import statistics

import click

from anomalica_common.llm import (
    accumulate,
    estimate_batch,
    estimate_record,
    get_usage,
    get_usage_trace,
    get_schema_enforcement,
    is_metered,
    is_opencode_model,
    is_openrouter_model,
    reset_schema_enforcement,
    reset_usage,
    resolve_use_api,
    check_allowance,
    headroom_for,
    weekly_reserve_for,
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
    "a production run. A variant is keyed by (model, prompt sha), so re-running "
    "under a DIFFERENT prompt writes a new variant and leaves the old one. The "
    "CANONICAL is a pointer to the chosen extraction and IS replaced by any "
    "production run - that is what makes a re-digest take effect.",
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
    "--run-label",
    default=None,
    help="Label a DELIBERATE REPEAT so it lands beside its twin instead of "
    "overwriting it. Without one, an identical (model, prompt) re-run overwrites "
    "its own variant - right for a redo, fatal for measuring run-to-run variance. "
    "A labelled run is always variant-only.",
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
    run_label: str | None,
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
    # Only price a run that actually costs per-token money. A flat-rate plan
    # (Claude subscription, opencode) has no price to quote, and asking for one
    # raises by design - refusing to guess is the GAP-2 behaviour.
    use_api = resolve_use_api(_USE_API_VAR)

    # ALLOWANCE CEILING, distinct from the spend gate below. That one guards
    # metered DOLLARS and is a no-op on the subscription transport; this guards
    # plan ALLOWANCE, which is precisely what a subscription run consumes. A
    # direct invocation had no ceiling of any kind - they lived only in the
    # scheduler's runner and the nightly script - so anything run outside those
    # consumed the plan until the provider itself throttled.
    #
    # Never BEGIN past the ceiling; an in-flight run finishes. Hitting a soft
    # ceiling is a pause that self-corrects when the window rolls. Running past it
    # hard-throttles the plan, which stops every component AND any supervising
    # session, since they share it - and then nothing is left to restart anything.
    if not use_api:
        # Headroom scales with the job: the ceiling governs whether a run STARTS,
        # so the peak is the ceiling plus whatever an admitted job goes on to
        # draw. A book admitted at 85% drew ten more points and was killed at the
        # wall with no artefact written.
        _body_chars = len(parsed.body or "")
        allowance = check_allowance(
            session_headroom=headroom_for(_body_chars),
            weekly_headroom=weekly_reserve_for(_body_chars),
        )
        if not allowance.ok:
            click.echo(f"Allowance ceiling: {allowance.reason}")
            if allowance.resets_at:
                click.echo(f"  window resets at {allowance.resets_at}")
            click.echo("  Not starting. Completed chunks are cached; resume is cheap.")
            ctx.exit(77)
        click.echo(f"Allowance ok ({allowance.reason})")

    if is_metered(model, use_api) and not spend_confirmed(
        estimate_record(len(parsed.body or ""), model),
        model,
        confirm,
        echo=click.echo,
        use_api=use_api,
    ):
        ctx.exit(2)

    # Cooperative cancel for the scheduler's hard-limit cancel: SIGTERM asks the
    # extraction to stop at the next chunk boundary (the in-flight call finishes
    # and is cached first), then we exit with a DEDICATED code so the scheduler
    # tells "cancelled - cache valid, cheap retry" from a real failure. Completed
    # chunks are already on disk; the resume replays them. Exit codes: 0 done,
    # 75 clean cancel, 1 real failure (see the anomalica/scheduler contract).
    import signal

    from digester.extract import ExtractionCancelled, request_cancel

    # Exit-code contract with anomalica/scheduler, whose dispatcher only ever sees
    # a process exit code: 0 done, 75 clean cancel (cache valid), 77 rate-limited,
    # 143/137 hard kill, 1 real failure, 2 spend gate refused. 77 is its own code
    # because on a FLAT plan throttling is the governor rather than spend - it must
    # park the lane and retry with backoff, never strike toward skip the way a
    # genuine extraction failure does. Grepping stderr for it would be the fragile
    # alternative.
    from anomalica_common.llm import OpencodeRateLimited, PlanRateLimited

    signal.signal(signal.SIGTERM, lambda *_: request_cancel())
    try:
        _do_extract(
            path,
            parsed,
            Path(output) if output else None,
            model,
            use_api,
            Path(digests_root) if digests_root else None,
            variant_only,
            Path(predigests_root) if predigests_root else None,
            run_label,
        )
    except ExtractionCancelled:
        click.echo(
            "\nCancelled at a chunk boundary. Completed chunks are cached; rerun the "
            "same (record, model, prompt) to resume - only the remaining chunks will "
            "call the model."
        )
        ctx.exit(75)
    except OpencodeRateLimited as e:
        click.echo(
            f"\nRate-limited by the opencode plan: {e}\nCompleted chunks are cached; "
            "park this lane and retry with backoff - this is NOT an extraction "
            "failure."
        )
        ctx.exit(77)
    except PlanRateLimited as e:
        # Same exit code as the opencode case, and for the same reason: on a flat
        # plan throttling is the governor, not spend. It must park the lane, never
        # strike toward skip - two strikes and the scheduler unstages a record that
        # was never broken. This matters more with concurrent workers, since N of
        # them reach the 5-hour cap N times faster.
        wait = e.seconds_until_reset()
        when = f" Window resets in ~{int(wait / 60)} min." if wait else ""
        click.echo(
            f"\nRate-limited by the Claude plan: {e}{when}\nCompleted chunks are "
            "cached; park this lane and retry after the reset - this is NOT an "
            "extraction failure."
        )
        ctx.exit(77)


def _normalise_locations(parsed, claims: list, echo=lambda _: None) -> None:
    """Rewrite claim locations to a canonical span, timed or not.

    Timed records (word timestamps present) resolve to HH:MM:SS.d ranges; every
    other record resolves to a char span in the pre-digest. Both come from the
    same aligner - its per-word array is just "a number per word", and nothing in
    the scoring cares whether those numbers are seconds or offsets.

    A claim whose quote will not align keeps whatever the model wrote. That is
    deliberate: an unalignable quote means the claim is not verbatim-present, and
    a fabricated span would be worse than an honest one we cannot canonicalise.
    """
    from anomalica_common.pre_digest import materialise
    from digester.realign import (
        normalise_claim_locations,
        normalise_untimed_locations,
        words_from_record2,
    )

    if not claims:
        return
    words, times = words_from_record2(parsed.body)
    if words:
        stats = normalise_claim_locations(claims, words, times)
        axis = "timecode"
    else:
        stats = normalise_untimed_locations(claims, materialise(parsed.body))
        axis = "char offset"
    echo(
        f"  locations -> {axis}: {stats['aligned']}/{stats['total']} aligned"
        f" ({stats['unaligned']} unalignable, {stats['ambiguous']} ambiguous)"
    )


def _review_provenance_for(record_md: Path) -> dict:
    """Review state for the digest stamp, resolved from the record's location.

    Tolerant by design: a stamp is provenance, not a gate, so a record whose
    ingests root cannot be resolved records "unknown" rather than failing the
    extraction or - worse - silently claiming the record was unreviewed.
    """
    from digester.review_gate import review_provenance

    try:
        ingests_dir = record_md.resolve().parent.parent
        return review_provenance(record_md, ingests_dir)
    except OSError:
        return {"state": "unknown", "sidecar": "unresolved"}


def _do_extract(
    path: Path,
    parsed,
    output: Path | None,
    model: str,
    use_api: bool = False,
    digests_root: Path | None = None,
    variant_only: bool = False,
    predigests_root: Path | None = None,
    run_label: str | None = None,
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
    reset_schema_enforcement()
    try:
        result = extract_two_pass(
            parsed.body,
            model=model,
            record_context=record_context,
            on_progress=click.echo,
            use_api=use_api,
        )

        # Canonicalise every claim's location from its verbatim quote, before the
        # digest is written. A model asked to say WHERE a claim came from invents
        # its own notation and no two models invent the same one - on real output,
        # haiku wrote "11" where sonnet wrote "line 11", and "file_page: 1" where
        # sonnet wrote "file_page 1, printed_page 5". Same line, same page, not one
        # shared string, so anything grouping claims by location saw two models that
        # never agreed. Aligning the quote discards the model's notation entirely,
        # which is why this cannot regress when a new model is added.
        _normalise_locations(parsed, result.get("claims") or [], click.echo)

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
            review=_review_provenance_for(path),
            record_extra={
                k: v
                for k in (
                    "release",
                    "provenance",
                    "classification",
                    "supersedes",
                    "speakers",
                    "pages",
                    "fetched_url",
                    "description",
                )
                if (v := parsed.metadata.get(k))
            },
            model=model,
            ai_usage=ai_usage,
            pre_digest={"sha256": pd_sha, "prep_version": PREP_VERSION},
            # Omitted (None) on the Anthropic paths, which enforce the schema by
            # construction; set to native/prompt/mixed for an OpenRouter run so a
            # cross-model comparison can tell enforcement apart from quality.
            schema_enforcement=(
                get_schema_enforcement()
                if (is_openrouter_model(model) or is_opencode_model(model))
                else None
            ),
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
                run_label=run_label,
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


# --- Eval: deterministic scoring of a digest against in-body highlight gold ---


@main.command(name="eval")
@click.argument("record", type=click.Path(exists=True))
@click.argument("digests", nargs=-1, type=click.Path(exists=True), required=True)
@click.option(
    "--json-out",
    type=click.Path(),
    default=None,
    help="Write the full per-digest results (with diagnostics) to this JSON file.",
)
@click.option(
    "--recall-threshold",
    default=None,
    type=float,
    help="Fraction of a gold highlight that claim spans must cover to count as "
    "recalled (default 0.5).",
)
@click.option(
    "--gold-json",
    type=click.Path(exists=True),
    default=None,
    help="Grade against an external gold set (a JSON with a `spans` list of "
    "`{text}`) instead of the record's in-body highlights. For PROVISIONAL "
    "model-drafted gold that screens hypotheses but does not decide (ADR 0042).",
)
def eval_cmd(
    record: str,
    digests: tuple[str, ...],
    json_out: str | None,
    recall_threshold: float | None,
    gold_json: str | None,
) -> None:
    """Grade one or more digests of RECORD against highlight gold.

    No model runs: the same digest always scores the same, so a prompt change's
    effect is a difference of two numbers. RECALL (did highlighted spans survive)
    and QUOTE FIDELITY (does each quote appear verbatim in the source) are
    gold-backed. OFF-TARGET rate (claims outside every highlight) is INTERPRETIVE,
    not an absolute precision score - a highlight set is a sample of what matters,
    not a complete keep-list (ADR 0042); read it as a relative signal between
    variants at equal recall. Pass several DIGESTS to compare models side by side.

    Gold is the record's in-body highlights by default; --gold-json grades against
    an external (e.g. provisional model-drafted) gold set instead.
    """
    import yaml

    from digester import eval as ev

    body = parse_record(Path(record).read_text()).body or ""
    thresh = recall_threshold if recall_threshold is not None else ev.RECALL_THRESH

    gold_texts = None
    if gold_json:
        gold_doc = json.loads(Path(gold_json).read_text())
        gold_texts = [s["text"] for s in gold_doc.get("spans", []) if s.get("text")]
        gold_n = len(gold_texts)
        provisional = gold_doc.get("provisional")
    else:
        gold_n = len([h for h in ev.parse_highlights(body) if h["text"]])
        provisional = False
    if gold_n == 0:
        click.echo(
            f"No highlight gold for {Path(record).name} - nothing to grade against. "
            "Highlights are authored in the workbench and stored in the record body."
        )
        raise SystemExit(1)

    results = []
    for d in digests:
        digest = yaml.safe_load(Path(d).read_text()) or {}
        r = ev.grade_digest(body, digest, recall_thresh=thresh, gold_texts=gold_texts)
        r["digest"] = Path(d).name
        r["model"] = digest.get("model", "?")
        results.append(r)

    def pct(x: float | None) -> str:
        return f"{x:>7.1%}" if isinstance(x, (int, float)) else f"{'n/a':>7}"

    if provisional:
        click.echo(
            "\n*** PROVISIONAL model-drafted gold - SCREENING ONLY. A keep/discard "
            "decision on a prompt change must cite human-signed gold (ADR 0042). ***"
        )
    click.echo(
        f"\nRecord: {Path(record).name}\n"
        f"Gold: {results[0]['gold_units']} units (= locatable highlights), "
        f"{results[0]['units_with_context']} carry context (max closure "
        f"{results[0]['max_context']})"
        + (
            f", {results[0]['unlocatable_gold']} highlight(s) not locatable in the pre-digest"
            if results[0]["unlocatable_gold"]
            else ""
        )
        + ".\n"
    )
    click.echo(
        f"{'model':18} {'claims':>6} {'recall':>7} {'mech-fid':>8} "
        f"{'e/r/b':>10} {'coref':>7} {'off-tgt':>8}"
    )
    click.echo("-" * 76)
    for r in results:
        erb = f"{r['elided']}/{r.get('reordered', 0)}/{r['broken']}"
        coref = (
            f"{r['coref_passed']}/{r['coref_applicable']}"
            if r["coref_applicable"]
            else "n/a"
        )
        click.echo(
            f"{str(r['model'])[:18]:18} {r['claims']:>6} {pct(r['recall'])} "
            f"{pct(r['quote_fidelity'])} {erb:>10} {coref:>7} {pct(r['off_target_rate'])}"
        )
    gu = results[0]["gold_units"]
    click.echo(
        "\nrecall + mech-fid are gold-backed; off-target is INTERPRETIVE "
        f"(relative signal only, against {gu} gold\nunits - a sparse-gold off-target "
        "is not comparable to a dense-gold one; ADR 0042).\n"
        "recall = COVERAGE-WEIGHTED mean over gold units (a unit half-covered scores "
        "50%, never hit/miss).\nmech-fid (MECHANICAL) = quote is contiguous or elided "
        "IN SOURCE ORDER. e/r/b = elided (ordered\n'...' join, faithful) / reordered "
        "(stitched OUT of order = quote-mining FAILURE) / broken (a\nfragment absent "
        "= fabricated FAILURE). coref = MECHANICAL coreference: passed/applicable "
        "dependent\nunits where a covering claim NAMED a referent (not the bare "
        "pronoun). mech-fid, semantic\ninversion, and the RIGHT-referent check are the "
        "human grader's axis - not these numbers; see\nthe coref_audit in --json-out "
        "to spot-check named vs correct referent."
    )

    if json_out:
        Path(json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        click.echo(f"\nFull results (with diagnostics): {json_out}")


# Anchored to the repo layout, NOT to the working directory. Relative defaults
# resolved against cwd, so the same command read the whole corpus from
# workspace/ and an EMPTY directory from the repo root - where the systemd
# service runs it. Its first timed run duly reported "No findings" over zero
# records, in 220ms, exit 0. A guard reporting clean because it found nothing to
# check is worse than no guard: it actively asserts health.
_ANOMALICA = Path(__file__).resolve().parents[3]


@main.command(name="health")
@click.option(
    "--digests",
    type=click.Path(),
    default=str(_ANOMALICA / "digests" / "records"),
    help="Canonical digests directory",
)
@click.option(
    "--store",
    type=click.Path(),
    default=str(_ANOMALICA / "ingests" / "store"),
    help="Ingest store (source sizes)",
)
@click.option(
    "--records",
    type=click.Path(),
    default=str(_ANOMALICA / "ingests" / "records"),
    help="Ingest records (annotation survival, frontmatter drift)",
)
def health_cmd(digests: str, store: str, records: str) -> None:
    """Report conditions a successful exit code cannot see.

    Every check here turns one silent success into a visible condition. They
    existed as a library with no caller, which makes them exactly as blind as a
    guard with a threshold that has drifted.
    """
    from digester import health

    d, s, r = Path(digests), Path(store), Path(records)
    findings = 0

    # An EMPTY corpus is a finding, never a pass. Every check below reports zero
    # when there is nothing to check, which is indistinguishable from a clean
    # bill of health - the same absence-read-as-a-value error these checks exist
    # to catch, in the reporting layer rather than the data.
    for label, path in (("digests", d), ("store", s), ("records", r)):
        if not path.is_dir() or not any(path.iterdir()):
            click.echo(f"CANNOT CHECK: {label} directory empty or missing - {path}")
            findings += 1
    if findings:
        click.echo(f"\n{findings} finding(s). No corpus was read.")
        raise SystemExit(1)

    loaded = health.load_digests(d)
    rows = health.claim_yields(d, s, loaded)
    by_type: dict[str, list[float]] = {}
    for row in rows:
        by_type.setdefault(row.get("medium") or "?", []).append(row["per_kb"])
    click.echo(f"Claim yield ({len(rows)} records >= {health.YIELD_MIN_KB}KB):")
    for t, v in sorted(by_type.items()):
        basis = "own" if len(v) >= health.YIELD_MIN_PER_TYPE else "inherited"
        click.echo(
            f"  {t:8} n={len(v):3}  median {statistics.median(v):5.2f} cl/KB  ({basis})"
        )

    for label, hits, fmt in (
        (
            "LOW YIELD (extraction probably failed despite exiting 0)",
            health.low_yield(d, s, loaded),
            lambda x: (
                f"{x['digest']}: {x['per_kb']:.2f} cl/KB vs "
                f"{x['floor_basis']} median {x['type_median']}"
            ),
        ),
        (
            "COLLAPSED CACHE PREFIX (correct output, broken cost characteristic)",
            health.collapsed(d, loaded=loaded),
            lambda x: f"{x['digest']}: read/write {x['ratio']:.2f} over {x['calls']} calls",
        ),
        (
            "OVER-MARKED (annotations remove most of the body before the model sees it)",
            health.over_marked(r) if r.exists() else [],
            lambda x: f"{x['record']}: {x['survives'] * 100:.1f}% of body survives",
        ),
    ):
        click.echo(f"\n{label}: {len(hits)}")
        for x in hits:
            findings += 1
            click.echo(f"  {fmt(x)}")

    stale = health.pre_digest_freshness(d, r, loaded)
    click.echo(
        f"\nSTALE PRE-DIGEST (digest no longer matches its source): {len(stale)}"
    )
    for x in stale:
        findings += 1
        click.echo(f"  {x['digest']}: {x['issue']} - {x['detail']}")

    unmapped = health.unmapped_record_fields(r) if r.exists() else {}
    click.echo(f"\nUNMAPPED RECORD FIELDS (upstream added something): {len(unmapped)}")
    for k, n in unmapped.items():
        findings += 1
        click.echo(f"  {k}: {n} records")

    click.echo(f"\n{findings} finding(s)." if findings else "\nNo findings.")
    raise SystemExit(1 if findings else 0)


if __name__ == "__main__":
    main()
