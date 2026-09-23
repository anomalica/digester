from __future__ import annotations

import json
import re
from pathlib import Path

import statistics

import click

from anomalica_common.llm import (
    RouteEnumLimit,
    ledger,
    accumulate,
    estimate_batch,
    estimate_record,
    get_usage,
    get_usage_trace,
    get_schema_enforcement,
    is_metered,
    is_openai_subscription_model,
    is_opencode_model,
    is_openrouter_model,
    reset_schema_enforcement,
    reset_usage,
    resolve_use_api,
    note_run_failure,
    check_allowance,
    headroom_for,
    weekly_reserve_for,
    spend_confirmed,
    usage_entry,
)
from anomalica_common.model_policy import PolicyRefusal
from digester.input_rights import (
    HostedInputAuthority,
    HostedInputRightsError,
    authorise_ordinary_extraction,
    hosted_route,
)
from digester.record_parser import parse_record

# The digester resolves its own metered toggle: DIGESTER_USE_API > global
# ANOMALICA_USE_API > subscription (per-component scheme; see
# /home/mark/repos/anomalica/AGENTS.md).
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


def _uses_claude_allowance(model: str, use_api: bool) -> bool:
    """Whether this call draws from the Claude subscription allowance."""
    return (
        not use_api
        and not is_opencode_model(model)
        and not is_openai_subscription_model(model)
        and not is_openrouter_model(model)
    )


def _authorise_hosted_input(
    path: Path,
    model: str,
    use_api: bool,
    evaluation_manifest: Path | None = None,
) -> HostedInputAuthority:
    try:
        if evaluation_manifest is None:
            return authorise_ordinary_extraction(path, model, use_api)
        from benchmarks.evaluation_corpus import authorise_dispatch

        route = hosted_route(model, use_api)
        admission = authorise_dispatch(
            evaluation_manifest,
            path,
            use="hosted-model-inference",
            provider=route.provider,
            route=route.route,
        )
        return admission["input_authority"]
    except (HostedInputRightsError, ValueError, OSError) as exc:
        raise click.ClickException(f"hosted input refused: {exc}") from exc


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
    help="Store the content-addressed materialised pre-digest (ADR 0042). "
    "Page-mapped record/3 extraction requires this and stores its canonical "
    "source map in the sibling source-maps/ directory.",
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
    "(required for any spend; see "
    "/home/mark/repos/anomalica/AGENTS.md spend gate)",
)
@click.option(
    "--evaluation-manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Use the canonical evaluation permission mechanism for this exact record and route.",
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
    evaluation_manifest: Path | None,
) -> None:
    """Extract knowledge from a record into a reviewable digest YAML file."""
    path = Path(file_path)
    text = path.read_text()

    click.echo(f"Parsing record: {path.name}")
    parsed = parse_record(text)

    # SPEND GATE (/home/mark/repos/anomalica/AGENTS.md operating rule): when this
    # run will hit the metered API, print a cost estimate and refuse to proceed
    # without an explicit --confirm. A promise/convention is not enough - this is
    # the gate.
    # Only price a run that actually costs per-token money. A flat-rate plan
    # (Claude subscription, opencode) has no price to quote, and asking for one
    # raises by design - refusing to guess is the GAP-2 behaviour.
    use_api = resolve_use_api(_USE_API_VAR)
    input_authority = _authorise_hosted_input(path, model, use_api, evaluation_manifest)

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
    if _uses_claude_allowance(model, use_api):
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
    from anomalica_common.llm import (
        OpencodeRateLimited,
        OpencodeTimedOut,
        PlanRateLimited,
    )

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
            input_authority,
        )
    except RouteEnumLimit as e:
        # A CONDITION OF THE ROUTE, NOT A FAILURE OF THE RECORD, so the record
        # is digested by the next model the policy permits rather than left in
        # the queue. Refusing quickly would only trade a slow failure for a fast
        # one; the corpus would still have the hole. The flat-rate lane exists to
        # spend allowance on records that need it, and a record whose node
        # directory overflows the route is exactly one that needs it.
        try:
            fallback = _next_permitted_model("digest", e.model)
        except PolicyRefusal as refusal:
            # A reroute that cannot legally happen is a defect in the policy, so
            # it stops here rather than quietly serving the next permitted model.
            note_run_failure()
            click.echo(f"\n{e}\n{refusal}")
            ctx.exit(1)
        if fallback is None:
            note_run_failure()
            click.echo(f"\n{e}\nNo permitted fallback for the digest stage.")
            ctx.exit(1)
        click.echo(
            f"\n{e}\nRerouting to {fallback} and re-extracting; the abandoned "
            f"attempt spent no metered money."
        )
        ledger.set_context(
            rerouted_from=e.model, reroute_reason=f"enum {e.members} > {e.limit}"
        )
        _do_extract(
            path,
            parsed,
            Path(output) if output else None,
            fallback,
            resolve_use_api(_USE_API_VAR),
            Path(digests_root) if digests_root else None,
            variant_only,
            Path(predigests_root) if predigests_root else None,
            run_label,
            _authorise_hosted_input(
                path,
                fallback,
                resolve_use_api(_USE_API_VAR),
                evaluation_manifest,
            ),
        )
    except ExtractionCancelled:
        note_run_failure()
        click.echo(
            "\nCancelled at a chunk boundary. Completed chunks are cached; rerun the "
            "same (record, model, prompt) to resume - only the remaining chunks will "
            "call the model."
        )
        ctx.exit(75)
    except OpencodeRateLimited as e:
        note_run_failure()
        click.echo(
            f"\nRate-limited by the opencode plan: {e}\nCompleted chunks are cached; "
            "park this lane and retry with backoff - this is NOT an extraction "
            "failure."
        )
        ctx.exit(77)
    except OpencodeTimedOut as e:
        note_run_failure()
        click.echo(
            f"\nOpenCode call timed out: {e}\nCompleted chunks are cached; park "
            "this lane and retry - this is NOT an extraction failure."
        )
        ctx.exit(77)
    except PlanRateLimited as e:
        note_run_failure()
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


def _next_permitted_model(stage: str, after: str) -> str | None:
    """The next model the policy permits for `stage`, skipping `after`.

    Walks the stage's own priority list in order rather than picking a
    favourite, so a reroute lands wherever the policy says the work should go
    next - and skips anything the policy would refuse, which is how a deny
    entry keeps applying to a fallback as much as to a first choice.
    """
    from anomalica_common import model_policy

    policy = model_policy.load()
    # The DECLARED target wins where the policy names one: a reroute is a
    # different question from a first choice, and the answer belongs in the
    # policy where it can be found, not in this function.
    declared = policy.reroute(stage)
    if declared and declared != after:
        return declared
    order = policy.priority(stage)
    start = order.index(after) + 1 if after in order else 0
    for candidate in order[start:]:
        if candidate != after and not policy.refusal(stage, candidate):
            return candidate
    return None


def _entail(text: str, pre_digest: str | None, echo=lambda _: None) -> str:
    """The last step of extraction: per-claim entailment (digester/entailment.py).

    Never lets the digest go: an extraction that just spent allowance is not
    lost to a post-step. Missing torch, a policy refusal, a model that fails to
    load, a CUDA error - each leaves the claims unassessed and says so. The
    CHECK_JSON line is the scheduler's machine record of the run (model ids,
    claim count, wall time), the local-stage twin of USAGE_JSON.
    """
    from digester import entailment

    if not entailment.enabled():
        return text
    if not entailment.available():
        echo("Entailment: torch/transformers not installed; claims left unassessed")
        return text
    try:
        checker = entailment.Checker()
    except PermissionError as e:
        echo(f"Entailment: {e}; claims left unassessed")
        return text
    try:
        out, counts = entailment.annotate_yaml(text, pre_digest, checker)
    except Exception as e:  # noqa: BLE001 - see docstring
        echo(f"Entailment: failed ({type(e).__name__}: {e}); claims left unassessed")
        return text
    finally:
        checker.release()
    echo(f"Entailment: {counts['assessed']} claims assessed, {counts['labels']}")
    echo(f"CHECK_JSON: {json.dumps(_check_record(counts))}")
    return out


def _check_record(counts: dict) -> dict:
    return {
        "stage": "check",
        "models": counts.get("models", []),
        "assessed": counts["assessed"],
        "kept": counts["skipped"],
        "unlocated": counts["unlocated"],
        "labels": counts["labels"],
        "duration_s": counts.get("duration_s", 0.0),
    }


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
    input_authority: HostedInputAuthority | None = None,
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
        SOURCE_MAPPED_PREP_VERSION,
        materialise,
        pre_digest_hash,
        prepare_page_record,
        store_source_map,
        store_pre_digest,
    )
    from digester.extract import (
        build_record_context,
        effective_extraction_configuration,
        extract_two_pass,
    )
    from digester.extraction_config_registry import fingerprint, register
    from digester.source_anchors import (
        anchor_claims,
        digest2_yaml,
        digest_record_extra,
        digest_record_snapshot,
        is_digest2_record,
        record3_structure,
        record_snapshot_yaml,
    )

    if input_authority is None:
        input_authority = authorise_ordinary_extraction(path, model, use_api)

    if digests_root is not None:
        from digester.authority import synchronise

        synchronise(digests_root)

    record_context = build_record_context(
        title=parsed.title,
        creators=parsed.creators,
        date=parsed.date,
        source_type=parsed.source_type,
    )

    # Validate record/3 structurally before any provider call. Eligible PDF/image
    # Records use the shared mapped producer; every other input remains on the
    # legacy digest/1 preparation path and cannot infer exact anchors.
    structure = record3_structure(parsed)
    snapshot = digest_record_snapshot(parsed, structure) if structure else None
    source_mapped = is_digest2_record(structure)
    prepared = prepare_page_record(structure, parsed.body) if source_mapped else None
    if source_mapped and predigests_root is None:
        raise click.ClickException(
            "page-mapped record/3 extraction requires --predigests-root so its "
            "content-addressed preparation-v9 source map can be retained"
        )

    # Pre-digest (ADR 0042/0051): the exact model input. Page-mapped output uses
    # labelled hashes and prep 9; legacy/non-paged output retains prep 8.
    pre_digest_text = prepared.text if prepared else materialise(parsed.body)
    prep_version = SOURCE_MAPPED_PREP_VERSION if prepared is not None else PREP_VERSION
    pd_sha = prepared.sha256 if prepared else pre_digest_hash(pre_digest_text)
    if predigests_root is not None:
        record_key = (parsed.frontmatter.get("content_hash") or path.stem).removeprefix(
            "sha256:"
        )
        if prepared is not None:
            store_source_map(predigests_root.parent / "source-maps", prepared)
        stored = store_pre_digest(
            predigests_root,
            record_key,
            pre_digest_text,
            prep_version=prep_version,
        )
        stored_sha = (
            f"sha256:{stored['predigest_sha256']}"
            if prepared
            else stored["predigest_sha256"]
        )
        if stored_sha != pd_sha:
            raise click.ClickException(
                "stored pre-digest hash disagrees with preparation"
            )

    click.echo(f"Extracting (two-pass) from: {parsed.title or path.name}")
    # What this run is, for its ledger row. The transport writes the row when
    # the accumulator is reset (below) or at exit, so a run started outside the
    # scheduler still reaches the History page - which is the whole point: the
    # benchmark scripts spawn this command with a metered key and --confirm.
    from anomalica_common.llm import ledger

    ledger.set_context(
        type="digest",
        ref=(parsed.metadata.get("content_hash") or "").split(":")[-1] or None,
        source_type=parsed.source_type,
        body_chars=len(parsed.body or ""),
        source="digester-direct",
    )
    reset_usage()
    reset_schema_enforcement()
    try:
        result = extract_two_pass(
            parsed.body,
            model=model,
            record_context=record_context,
            on_progress=click.echo,
            use_api=use_api,
            input_authority=input_authority,
            prepared_text=pre_digest_text,
            source_mapped=source_mapped,
        )

        # Canonicalise every claim's location from its verbatim quote, before the
        # digest is written. A model asked to say WHERE a claim came from invents
        # its own notation and no two models invent the same one - on real output,
        # haiku wrote "11" where sonnet wrote "line 11", and "file_page: 1" where
        # sonnet wrote "file_page 1, printed_page 5". Same line, same page, not one
        # shared string, so anything grouping claims by location saw two models that
        # never agreed. Aligning the quote discards the model's notation entirely,
        # which is why this cannot regress when a new model is added.
        if prepared is not None:
            anchored, rejected = anchor_claims(result.get("claims") or [], prepared)
            result["claims"] = anchored
            click.echo(
                f"  exact source anchors: {len(anchored)} anchored, "
                f"{len(rejected)} rejected"
            )
            for failure in rejected:
                click.echo(
                    f"    rejected anchor: {failure['reason']} ({failure['text']!r})"
                )
        else:
            _normalise_locations(parsed, result.get("claims") or [], click.echo)

        # Public AI-usage provenance (ADR 0037 inline emission): this digest's
        # extract entry, carried forward onto any upstream chain the ingest
        # record already published (record -> digest -> article).
        upstream = parsed.metadata.get("ai_usage")
        ai_usage = accumulate(
            upstream if isinstance(upstream, list) else None,
            usage_entry("digest", model, get_usage()),
        )

        config_parameters = {
            "prep_version": prep_version,
            "use_api": use_api,
            "schema_enforcement": (
                get_schema_enforcement()
                if (is_openrouter_model(model) or is_opencode_model(model))
                else None
            ),
        }
        effective_config = effective_extraction_configuration(
            model, **config_parameters
        )
        config_fingerprint = fingerprint(effective_config)

        text = two_pass_result_to_yaml(
            result,
            record_title=parsed.title,
            record_producer=_producer_from_creators(parsed.creators),
            record_publisher=parsed.publisher,
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
                **{
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
                # COPYRIGHT STATUS, flattened from the record's nested `copyright.status`.
                #
                # Reverses an earlier ruling of mine that this deliberately had ONE home
                # in the ingest record, on the grounds that a copy in a public artefact
                # becomes a staler second source of truth for an access decision. That
                # reasoning is still sound and it was still wrong, because the cost
                # landed elsewhere: with the graph unable to see copyright at all, the
                # assimilator came close to publishing verbatim excerpts from 13
                # copyrighted books. A field that must be joined is a field that gets
                # forgotten, and 0 of 100 graph records carrying a status is the proof.
                #
                # Flattened deliberately: the digest carries the STATUS only, not the
                # whole copyright block, so nothing else in it is republished.
                #
                # KNOWN HAZARD, and it is the reason the original ruling existed: this
                # lives in frontmatter, and frontmatter changes are invisible to
                # `stale-records` (pre_digest.sha256 covers the BODY). A licence that
                # changes after digestion leaves every digest asserting the old status
                # with nothing able to detect it. The metadata-refresh path must cover
                # this field, and an access decision at publish time should still read
                # the store rather than trusting this snapshot.
                **(
                    {"copyright_status": _cp["status"]}
                    if snapshot is None
                    and isinstance(_cp := parsed.metadata.get("copyright"), dict)
                    and _cp.get("status")
                    else {}
                ),
                **(digest_record_extra(snapshot) if snapshot else {}),
            },
            model=model,
            ai_usage=ai_usage,
            pre_digest={
                "sha256": pd_sha,
                "prep_version": prep_version,
                **(
                    {"source_map_sha256": prepared.source_map_sha256}
                    if prepared
                    else {}
                ),
            },
            # Omitted (None) on the Anthropic paths, which enforce the schema by
            # construction; set to native/prompt/mixed for an OpenRouter run so a
            # cross-model comparison can tell enforcement apart from quality.
            schema_enforcement=(
                get_schema_enforcement()
                if (is_openrouter_model(model) or is_opencode_model(model))
                else None
            ),
            extraction_config=config_fingerprint,
        )

        if prepared is not None:
            if snapshot is None:  # pragma: no cover - source_mapped implies record/3
                raise click.ClickException("digest/2 has no Record snapshot")
            text = digest2_yaml(text, result.get("claims") or [], prepared, snapshot)
        elif snapshot is not None:
            text = record_snapshot_yaml(text, snapshot)

        from digester.generation import stamp as stamp_extraction_generation

        text = stamp_extraction_generation(text)

        text = _entail(text, pre_digest_text, click.echo)

        if digests_root is not None:
            from digester import digest_store

            register(digests_root, effective_config)
            written = digest_store.write_digest(
                digests_root,
                output.stem if output else path.stem,
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

        # Stamp run_kind HERE too, not only on the digests_root path. Only
        # digest_store.write_digest stamped it, so any digest written with -o
        # carried no run_kind at all - which is most of why the nine digests of
        # 19 August cannot be attributed to a run today. A field that exists on
        # one write path and not the other is worse than one that exists on
        # neither: its absence reads as "old artefact" rather than "other path".
        from digester import digest_store  # local, matching the branch above

        out_path = output if output else path.with_suffix(".yaml")
        from digester.authority import synchronise

        synchronise(out_path.parent)
        register(out_path.parent, effective_config)
        kind = (
            "production"
            if digest_store.is_active_prompt(result.get("prompts"))
            else "comparison"
        )
        out_path.write_text(f"run_kind: {kind}\n{text}")
        click.echo(f"\nWritten to: {out_path} (run_kind: {kind})")
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
    help="Store each content-addressed materialised pre-digest (ADR 0042). "
    "Page-mapped record/3 extraction also stores the canonical source map in "
    "the sibling source-maps/ directory.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Confirm the printed aggregate cost estimate and proceed with the "
    "metered run (required for any spend; see "
    "/home/mark/repos/anomalica/AGENTS.md spend gate)",
)
@click.option(
    "--evaluation-manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Use the canonical evaluation permission mechanism for each exact record and route.",
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
    evaluation_manifest: Path | None,
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
    authorities = [
        _authorise_hosted_input(path, model, use_api, evaluation_manifest)
        for path, _ in parsed_records
    ]
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

    for i, ((p, parsed), input_authority) in enumerate(
        zip(parsed_records, authorities), 1
    ):
        click.echo(f"\n[{i}/{len(parsed_records)}] {p.name}")
        out = out_dir / p.with_suffix(".yaml").name if out_dir else None
        _do_extract(
            p,
            parsed,
            out,
            model,
            use_api,
            root,
            variant_only,
            pd_root,
            input_authority=input_authority,
        )


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

    parsed = parse_record(Path(record).read_text())
    body = parsed.body or ""
    thresh = recall_threshold if recall_threshold is not None else ev.RECALL_THRESH

    gold_texts = None
    gold_document = None
    record_hash = parsed.metadata.get("content_hash")
    if gold_json:
        gold_doc = json.loads(Path(gold_json).read_text())
        if gold_doc.get("schema") == "anomalica/highlight-gold/1":
            from digester.highlight_gold import validate

            gold_document = gold_doc
            gold_n = validate(record_hash or "", body, gold_doc)["gold_units"]
            provisional = False
        else:
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
        r = ev.grade_digest(
            body,
            digest,
            recall_thresh=thresh,
            gold_texts=gold_texts,
            gold_document=gold_document,
            record_hash=record_hash,
        )
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
    if gold_document is not None:
        denominator = results[0]["precision_denominator"]
        click.echo(
            "\nAuthenticated bounded gold: unsupported-assertion candidates use "
            f"only the {denominator if denominator is not None else 0} claims wholly "
            "inside complete attested ranges. Semantic precision remains n/a without "
            "fact-level adjudication."
        )
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


@main.command(name="eval-fixtures")
@click.argument("fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--variant",
    "variants",
    multiple=True,
    required=True,
    metavar="NAME=PATH",
    help="Named digest YAML/JSON prediction; repeat to compare variants.",
)
@click.option(
    "--adjudication",
    "adjudications",
    multiple=True,
    metavar="NAME=PATH",
    help="Hash-bound semantic sidecar for the named variant; repeat as needed.",
)
@click.option("--case", "case_id", default=None, help="Score only one fixture case id.")
@click.option(
    "--json-out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write complete machine-readable results.",
)
def eval_fixtures_cmd(
    fixture: Path,
    variants: tuple[str, ...],
    adjudications: tuple[str, ...],
    case_id: str | None,
    json_out: Path | None,
) -> None:
    """Score digest variants against narrative behaviour fixtures.

    This command never invokes a model. It can consume separately produced human
    or model semantic adjudications bound to the exact fixture and digest bytes.
    """
    from digester import fixture_eval

    try:
        fixture_document, fixture_hash = fixture_eval.load_hashed_document(fixture)
        fixture_eval.validate_fixture(fixture_document)
        parsed_variants: list[tuple[str, Path]] = []
        variant_names: set[str] = set()
        for binding in variants:
            name, separator, raw_path = binding.partition("=")
            name = name.strip()
            if not separator or not name or not raw_path.strip():
                raise fixture_eval.FixtureError("--variant must be NAME=PATH")
            if name in variant_names:
                raise fixture_eval.FixtureError(f"duplicate variant name {name}")
            variant_names.add(name)
            parsed_variants.append((name, Path(raw_path)))

        adjudication_paths: dict[str, Path] = {}
        for binding in adjudications:
            name, separator, raw_path = binding.partition("=")
            name = name.strip()
            if not separator or not name or not raw_path.strip():
                raise fixture_eval.FixtureError("--adjudication must be NAME=PATH")
            if name in adjudication_paths:
                raise fixture_eval.FixtureError(
                    f"duplicate adjudication for variant {name}"
                )
            adjudication_paths[name] = Path(raw_path)
        unknown = set(adjudication_paths) - variant_names
        if unknown:
            raise fixture_eval.FixtureError(
                f"adjudications name unknown variants {sorted(unknown)}"
            )

        results = []
        for name, path in parsed_variants:
            case_ids = [case["id"] for case in fixture_document.get("cases") or []]
            predictions, prediction_hashes = fixture_eval.load_prediction_inputs(
                path, case_ids, case_id=case_id
            )
            adjudication = None
            if name in adjudication_paths:
                adjudication_path = adjudication_paths[name]
                adjudication = fixture_eval.validate_adjudications(
                    fixture_eval.load_document(adjudication_path),
                    fixture_sha256=fixture_hash,
                    prediction_sha256=prediction_hashes,
                    fixture=fixture_document,
                    predictions=predictions,
                )
            result = fixture_eval.score(
                fixture_document,
                predictions,
                case_id=case_id,
                adjudications=adjudication,
            )
            result["variant"] = name
            result["prediction"] = str(path)
            result["adjudication"] = (
                str(adjudication_paths[name]) if name in adjudication_paths else None
            )
            results.append(result)
    except fixture_eval.FixtureError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(fixture_eval.format_report(results))
    if json_out is not None:
        json_out.write_text(fixture_eval.json_report(results))
        click.echo(f"\nFull results: {json_out}")


_FIXTURE_EVAL_ROOT = Path(__file__).resolve().parents[1] / "benchmarks/digestion-eval"
_FIXTURE_REPORT_ROOT = Path(__file__).resolve().parents[2] / "reports/digestion-eval"
_FIXTURE_STUB_BASELINE = _FIXTURE_EVAL_ROOT / "stub-baseline.json"
_FIXTURE_QUALITY_BASELINE = _FIXTURE_EVAL_ROOT / "quality-baseline.json"


@main.command(name="fixture-experiment")
@click.option(
    "--fixture",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=_FIXTURE_EVAL_ROOT / "cases.yaml",
    show_default=True,
)
@click.option(
    "--baseline",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Comparison baseline; defaults to stub baseline with --stub-responses, otherwise quality baseline.",
)
@click.option(
    "--output-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=_FIXTURE_REPORT_ROOT,
    show_default=True,
)
@click.option("--model", default="sonnet", show_default=True)
@click.option(
    "--stub-responses",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Run production extraction with canned transport responses and no provider call.",
)
@click.option(
    "--adjudication",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional exact-input-bound semantic adjudication sidecar.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Approve the printed aggregate estimate for a metered model run.",
)
@click.pass_context
def fixture_experiment_cmd(
    ctx: click.Context,
    fixture: Path,
    baseline: Path | None,
    output_root: Path,
    model: str,
    stub_responses: Path | None,
    adjudication: Path | None,
    confirm: bool,
) -> None:
    """Run production extraction over fixtures and compare the accepted baseline."""
    from digester.fixture_experiment import ExperimentError, run_experiment

    use_api = False if stub_responses is not None else resolve_use_api(_USE_API_VAR)
    selected_baseline = baseline or (
        _FIXTURE_STUB_BASELINE
        if stub_responses is not None
        else _FIXTURE_QUALITY_BASELINE
    )

    def dispatch_gate(prepared: list[dict]) -> dict[str, object]:
        authorities = {
            item["id"]: _authorise_hosted_input(item["path"], model, use_api)
            for item in prepared
        }
        if stub_responses is not None:
            return authorities
        source_chars = [len(item["parsed"].body or "") for item in prepared]
        total_chars = sum(source_chars)
        if _uses_claude_allowance(model, use_api):
            allowance = check_allowance(
                session_headroom=headroom_for(total_chars),
                weekly_headroom=weekly_reserve_for(total_chars),
            )
            if not allowance.ok:
                raise ExperimentError(f"allowance ceiling: {allowance.reason}")
            click.echo(f"Allowance ok ({allowance.reason})")
        if is_metered(model, use_api) and not spend_confirmed(
            estimate_batch(source_chars, model),
            model,
            confirm,
            echo=click.echo,
            use_api=use_api,
        ):
            raise ExperimentError("metered fixture experiment was not approved")
        return authorities

    try:
        result = run_experiment(
            fixture,
            selected_baseline,
            output_root,
            model=model,
            use_api=use_api,
            dispatch_gate=dispatch_gate,
            stub_responses=stub_responses,
            adjudication_path=adjudication,
        )
    except (ExperimentError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = (result["run_dir"] / "summary.txt").read_text()
    click.echo(summary, nl=False)
    click.echo(f"Artifacts: {result['run_dir']}")
    if result["report"]["baseline_comparison"]["outcome"] in {"worse", "mixed"}:
        ctx.exit(1)


@main.command(name="fixture-accept-baseline")
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--fixture",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=_FIXTURE_EVAL_ROOT / "cases.yaml",
    show_default=True,
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_FIXTURE_QUALITY_BASELINE,
    show_default=True,
)
def fixture_accept_baseline_cmd(report: Path, fixture: Path, output: Path) -> None:
    """Accept a validated genuine experiment report as the quality baseline."""
    from digester.fixture_experiment import ExperimentError, accept_quality_baseline

    if output.resolve() == _FIXTURE_STUB_BASELINE.resolve():
        raise click.ClickException(
            "quality acceptance cannot overwrite the stub baseline"
        )
    try:
        baseline = accept_quality_baseline(report, fixture, output)
    except (ExperimentError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Accepted quality baseline from {baseline['source']['run_id']}: {output}"
    )


@main.command(name="gold-batch")
@click.argument("record", type=click.Path(exists=True))
@click.argument("gold_json", type=click.Path(exists=True))
@click.option(
    "--digest",
    "digest_paths",
    multiple=True,
    type=click.Path(exists=True),
    help="Current digest whose overlapping claims may be shown as proposals.",
)
@click.option(
    "--range-id",
    default=None,
    help="Review range; defaults to the first incomplete range.",
)
@click.option("--limit", default=5, type=click.IntRange(3, 5), show_default=True)
def gold_batch_cmd(
    record: str,
    gold_json: str,
    digest_paths: tuple[str, ...],
    range_id: str | None,
    limit: int,
) -> None:
    """Emit the next compact human-gold batch without invoking a model."""
    import yaml

    from digester.highlight_gold import HighlightGoldError, review_batch

    parsed = parse_record(Path(record).read_text())
    document = json.loads(Path(gold_json).read_text())
    digests = [yaml.safe_load(Path(path).read_text()) or {} for path in digest_paths]
    try:
        result = review_batch(
            parsed.metadata.get("content_hash") or "",
            parsed.body or "",
            document,
            digests,
            range_id=range_id,
            limit=limit,
        )
    except HighlightGoldError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@main.command(name="sync-authority")
@click.argument("digests_root", type=click.Path(file_okay=False, path_type=Path))
def sync_authority_cmd(digests_root: Path) -> None:
    """Materialise current generation and Sonnet/Opus config authority."""
    from digester.authority import synchronise

    click.echo(json.dumps(synchronise(digests_root), indent=2, sort_keys=True))


@main.command(name="validate-output")
@click.argument(
    "digests_root", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.argument("digest_path", type=click.Path(exists=True, path_type=Path))
@click.argument("record_path", type=click.Path(exists=True, path_type=Path))
def validate_output_cmd(
    digests_root: Path, digest_path: Path, record_path: Path
) -> None:
    """Prove one canonical output matches exact authority and record input."""
    from digester.authority import AuthorityError, validate_output

    try:
        result = validate_output(digests_root, digest_path, record_path)
    except AuthorityError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2, sort_keys=True))


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
    default=str(_ANOMALICA / "digests"),
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
    default=str(_ANOMALICA / "ingests" / "by-name"),
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

    load_issues: list[dict] = []
    loaded = health.load_digests(d, load_issues)
    from digester.generation import GenerationManifestError, read_manifest

    try:
        current_generation = read_manifest(d)
        click.echo(f"Generation manifest: current {current_generation}")
    except GenerationManifestError as exc:
        current_generation = None
        findings += 1
        click.echo(f"Generation manifest: INVALID - {exc}")
    generations = health.extraction_generation_freshness(loaded, current_generation)
    generations["invalid"].extend(
        {
            "digest": issue["digest"],
            "generation": None,
            "reason": f"digest_{issue['issue']}",
        }
        for issue in load_issues
    )
    generation_total = sum(len(rows) for rows in generations.values())

    click.echo(
        f"Extraction generation (manifest current {current_generation}; "
        f"denominator {generation_total} canonical digest files):"
    )
    for status in ("current", "stale", "unknown", "invalid"):
        rows = generations[status]
        click.echo(f"  {status:7} {len(rows):3}/{generation_total}")
        if status != "current":
            for row in rows:
                findings += 1
                value = row.get("generation")
                detail = "absent" if value is None else repr(value)
                if row.get("distance") is not None:
                    detail += f" (distance {row['distance']})"
                if row.get("reason"):
                    detail += f" ({row['reason']})"
                click.echo(f"    {row['digest']}: {detail}")

    from digester.extraction_config_registry import (
        ExtractionConfigRegistryError,
        read_registry,
    )

    try:
        registry = read_registry(d)
        click.echo(f"\nExtraction configuration registry: {len(registry)} entries")
    except ExtractionConfigRegistryError as exc:
        registry = {}
        findings += 1
        click.echo(f"\nExtraction configuration registry: INVALID - {exc}")
    configs = health.extraction_config_freshness(loaded, registry)
    configs["invalid"].extend(
        {
            "digest": issue["digest"],
            "extraction_config": None,
            "reason": f"digest_{issue['issue']}",
        }
        for issue in load_issues
    )
    click.echo(f"Extraction configuration (denominator {generation_total} digests):")
    for status in ("resolvable", "unregistered", "missing", "invalid"):
        rows = configs[status]
        click.echo(f"  {status:12} {len(rows):3}/{generation_total}")
        if status != "resolvable":
            findings += len(rows)
            for row in rows:
                detail = row.get("reason") or ""
                click.echo(f"    {row['digest']}{': ' + detail if detail else ''}")

    _sc = health._cache_load()
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

    surv = [
        f
        for f in (health.pre_digest_survival(p, _sc) for p in sorted(r.glob("*.md")))
        if f is not None
    ]
    if surv:
        click.echo(
            f"\nPre-digest survival ({len(surv)} records): "
            f"min {min(surv):.3f}  median {statistics.median(surv):.3f}  "
            f"floor {health.SURVIVAL_FLOOR}"
        )

    input_groups = health.pre_digest_input_freshness(d, r, loaded)
    input_groups["invalid"].extend(
        {
            "digest": issue["digest"],
            "issue": f"digest_{issue['issue']}",
            "detail": issue["detail"],
        }
        for issue in load_issues
    )
    input_total = sum(len(rows) for rows in input_groups.values())
    click.echo(f"\nPre-digest input binding (denominator {input_total} digests):")
    for status in ("current", "stale", "unknown", "invalid"):
        rows = input_groups[status]
        click.echo(f"  {status:7} {len(rows):3}/{input_total}")
        if status != "current":
            findings += len(rows)
            for row in rows:
                click.echo(f"    {row['digest']}: {row['issue']} - {row['detail']}")

    unmapped = health.unmapped_record_fields(r) if r.exists() else {}
    click.echo(f"\nUNMAPPED RECORD FIELDS (upstream added something): {len(unmapped)}")
    for k, n in unmapped.items():
        findings += 1
        click.echo(f"  {k}: {n} records")

    click.echo(f"\n{findings} finding(s)." if findings else "\nNo findings.")
    raise SystemExit(1 if findings else 0)


@main.command(name="stale-records")
@click.option(
    "--digests",
    type=click.Path(),
    default=str(_ANOMALICA / "digests"),
    help="Canonical digests directory",
)
@click.option(
    "--records",
    type=click.Path(),
    default=str(_ANOMALICA / "ingests" / "by-name"),
    help="Ingest records directory",
)
def stale_records_cmd(digests: str, records: str) -> None:
    """Print records whose digest was built from text they no longer have.

    Composes with batch-extract:

        digester stale-records | xargs -r digester batch-extract --digests-root ...

    The queue's normal skip is "has a digest" and FORCE's is "has a
    provenance_chain". Neither is the right test for a record whose SOURCE moved
    underneath an otherwise complete digest, so that work kept being tracked in
    hand-maintained lists. This derives it from the artefacts instead.
    """
    from digester import health

    for p in health.stale_record_paths(Path(digests), Path(records)):
        click.echo(str(p))


@main.command(name="grade-record")
@click.argument("record", type=click.Path(exists=True))
@click.option(
    "--digests-root",
    type=click.Path(),
    default=str(_ANOMALICA / "digests"),
    help="Digests repo holding variants/",
)
def grade_record_cmd(record: str, digests_root: str) -> None:
    """Grade every variant of RECORD against its own reviewer highlights.

    Self-service on purpose: any component that dispatches variants can score
    them without waiting on this one. Variants are read from the standard
    location - digests/variants/{friendly-name}/*.yaml - so a dispatcher that
    writes there needs no handoff and no signal.

    Scores are comparable WITHIN a record and not across records. Measured
    2026-09-01: the same model varies 24-40 points between records while four
    models differ by 21 points on one record, so a table mixing records ranks
    whichever model drew the easier text.
    """
    import yaml as _yaml

    from digester.eval import grade_digest

    path = Path(record)
    body = parse_record(path.read_text(errors="replace")).body
    stem = path.name.removesuffix(".v2.md").removesuffix(".md")
    vdir = Path(digests_root) / "variants" / stem
    files = sorted(vdir.glob("*.yaml")) if vdir.is_dir() else []
    if not files:
        click.echo(f"No variants at {vdir}")
        raise SystemExit(1)
    from digester.digest_store import split_variant_stem

    rows = []
    for f in files:
        d = _yaml.safe_load(f.read_text())
        r = grade_digest(body, d)
        model, prompt_sha, label = split_variant_stem(f.stem)
        # The FILENAME hashes the prompts alone, so two artefacts can share a
        # fingerprint and differ in schema and code - which is the confound the
        # prompt column was added to catch, one level down. The body carries the
        # whole configuration when it was written after that was recorded;
        # before then there is no answer, and "no answer" is its own value.
        raw_config = d.get("extraction_config")
        cfg = (
            raw_config.get("config", "-")
            if isinstance(raw_config, dict)
            else raw_config or "-"
        )
        rows.append(((f"{model} {label}".strip(), prompt_sha, cfg), r))
    # A record with no reviewer highlights grades every variant at recall None;
    # those sort last and print n/a rather than crashing the whole table.
    rows.sort(key=lambda x: (x[1]["recall"] is None, -(x[1]["recall"] or 0.0)))
    g = rows[0][1]
    click.echo(f"{stem}")
    click.echo(
        f"  {g['gold_units']} gold units, {g['units_with_context']} with context chains"
    )
    if not g["gold_units"]:
        click.echo("  no reviewer highlights: recall cannot be scored here")
    click.echo(
        f"\n{'model':26} {'prompt':>9} {'config':>9} {'recall':>7} "
        f"{'fidelity':>9} {'coref':>7} {'claims':>7}"
    )

    def _f(v: float | None, width: int) -> str:
        return f"{v:{width}.3f}" if isinstance(v, (int, float)) else f"{'n/a':>{width}}"

    for (name, prompt_sha, cfg), r in rows:
        click.echo(
            f"{name[:26]:26} {prompt_sha or '?':>9} {cfg:>9} {_f(r['recall'], 7)} "
            f"{_f(r['quote_fidelity'], 9)} {_f(r['coref_rate'], 7)} {r['claims']:7}"
        )
    # A grid built across a prompt or schema change ranks that change, not the
    # model, and nothing else in this table would show it: the rows look
    # identical. Warn on either column, and count "-" as a value of its own -
    # an artefact written before the configuration was recorded is not known to
    # match one written after, it is simply unlabelled.
    for label, values in (
        ("PROMPT", {sha for (_, sha, _), _ in rows if sha}),
        ("CONFIGURATION", {c for (_, _, c), _ in rows}),
    ):
        if len(values) > 1:
            click.echo(
                f"\nTWO {label} VERSIONS IN THIS TABLE "
                f"({', '.join(sorted(values))}). Rows that differ there are NOT "
                "comparable - it is a variable like the model. Compare within "
                "one value, or re-run the gaps at one configuration."
            )
    click.echo(
        "\nComparable within this record only. MEASURED NOISE FLOOR: three "
        "identical Sonnet 5 runs of the Fowler interview (2026-09-07, cache "
        "off) spread 3.9 points of recall - 0.826, 0.831, 0.865 - with claim "
        "counts of 224, 242 and 257. A range from three samples understates "
        "the true spread, so read 3.9 as a lower bound: a gap of that size or "
        "less between two models says nothing at all.\nRULE: no recall "
        "difference measured on ONE record counts as a finding. A recall claim "
        "needs many records or repeated arms, and must say which it had. "
        "Fidelity varied 0.34 points across those arms, so grounding "
        "comparisons survive at sizes where recall comparisons do not."
    )


@main.command(name="selftest")
@click.argument("file_path", type=click.Path(exists=True))
def selftest_cmd(file_path: str) -> None:
    """Serialise one record end to end with NO model call, then exit.

    A crash AFTER the model call is the most expensive failure shape the pipeline
    has: the work is done, the allowance is spent, and the result is thrown away.
    That is not hypothetical - copyright_status was passed as a top-level keyword
    instead of into record_extra, and 30 overnight attempts each ran both passes,
    aligned the offsets, spent roughly $0.87 of plan-equivalent, and died at
    serialisation. Zero output, ~$26 of allowance, and a green test suite
    throughout because no fixture carried a copyright block.

    Run this against a REAL record before dispatching a batch. It exercises the
    same _do_extract path with the model stubbed, so every field that record
    carries reaches the serialiser exactly as it would in a paid run - and any
    signature mismatch costs a second rather than a batch.
    """
    import tempfile

    import yaml

    import digester.extract as ex

    path = Path(file_path)
    parsed = parse_record(path.read_text(errors="replace"))
    real = ex.extract_two_pass
    ex.extract_two_pass = lambda *a, **k: {
        "nodes": [],
        "claims": [],
        "terminology": [],
        "prompts": [],
    }
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "selftest.yaml"
            _do_extract(path, parsed, out, "haiku", False, None, False, None, None)
            doc = yaml.safe_load(out.read_text())
    except Exception as exc:
        click.echo(f"SELFTEST FAILED on {path.name}: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    finally:
        ex.extract_two_pass = real
    carried = sorted((doc.get("record") or {}).keys())
    click.echo(f"selftest ok: {path.name}")
    click.echo(f"  record block carries: {', '.join(carried)}")


@main.command(name="check")
@click.argument("digests", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--digests-root",
    type=click.Path(),
    default=str(_ANOMALICA / "digests"),
    help="Digests repo; with --all, every canonical digest in it",
)
@click.option(
    "--all", "all_", is_flag=True, help="Every canonical digest under --digests-root"
)
@click.option("--variants", is_flag=True, help="With --all, the variants/ tree as well")
@click.option(
    "--records",
    type=click.Path(),
    default=str(_ANOMALICA / "ingests" / "by-name"),
    help="Ingest records directory (the store beside it is read by hash)",
)
@click.option(
    "--model",
    "stage1_model",
    default=None,
    help="Stage-one classifier (quote premise); default: the policy's first choice",
)
@click.option(
    "--stage2-model",
    default=None,
    help="Stage-two classifier (record window); default: the policy's second choice",
)
@click.option(
    "--force", is_flag=True, help="Re-assess claims that already carry a verdict"
)
@click.option(
    "--redo-unlocated",
    is_flag=True,
    help="Only claims left at neutral/quote (quote not located) get the window stage",
)
@click.option(
    "--dry-run", is_flag=True, help="List digests that need a check; write nothing"
)
@click.option("--device", default=None, help="cuda | cpu (default: cuda if available)")
def check_cmd(
    digests: tuple[str, ...],
    digests_root: str,
    all_: bool,
    variants: bool,
    records: str,
    stage1_model: str | None,
    stage2_model: str | None,
    force: bool,
    dry_run: bool,
    device: str | None,
    redo_unlocated: bool,
) -> None:
    """Annotate existing digests with per-claim entailment.

    The same step `extract` runs last, applied to digests that predate it.
    Local and deterministic: no model calls, no allowance. Claims that already
    carry a verdict are left alone unless --force. Takes the graphics card for
    the duration; say so on the bus before a corpus-wide run.

    Exit codes, for the scheduler: 0 assessed and written; 3 nothing to do
    (every eligible claim already carried a verdict); 1 at least one digest
    failed (the rest were still written); 2 cannot run (torch missing, the
    model policy refuses the classifier, no digests named). One CHECK_JSON
    line per digest carries model ids, claim count, label mix and wall time.
    """
    import yaml

    from digester import entailment
    from digester.health import _Loader

    root = Path(digests_root)
    paths = [Path(p) for p in digests]
    if all_:
        paths += sorted(root.glob("*.yaml"))
        if variants:
            paths += sorted((root / "variants").glob("*/*.yaml"))
    if not paths:
        click.echo("nothing to check: give digest paths or --all")
        raise SystemExit(2)

    def load(p: Path) -> dict | None:
        try:
            doc = yaml.load(p.read_text(), Loader=_Loader)
        except (OSError, yaml.YAMLError) as e:
            click.echo(f"  skip {p.name}: {type(e).__name__}: {e}")
            return None
        return doc if isinstance(doc, dict) else None

    if dry_run:
        due = [
            p
            for p in paths
            if (d := load(p)) is not None and (force or entailment.needs_check(d))
        ]
        for p in due:
            click.echo(str(p))
        click.echo(f"\n{len(due)} of {len(paths)} digest(s) need a check")
        raise SystemExit(0 if due else 3)

    if not entailment.available():
        click.echo("torch/transformers not installed; nothing assessed")
        raise SystemExit(2)
    try:
        checker = entailment.Checker(
            device,
            stage1_model or entailment.STAGE1_MODEL,
            stage2_model or entailment.STAGE2_MODEL,
        )
    except PermissionError as e:
        click.echo(str(e))
        raise SystemExit(2) from e

    totals: dict[str, int] = {}
    files = assessed = failed = 0
    try:
        for p in paths:
            text = p.read_text()
            doc = load(p)
            if doc is None:
                failed += 1
                continue
            pre = entailment.pre_digest_for(doc, Path(records))
            try:
                out, counts = entailment.annotate_yaml(
                    text, pre, checker, force=force, redo_unlocated=redo_unlocated
                )
            except Exception as e:  # noqa: BLE001 - reported per file, batch continues
                click.echo(f"  FAILED {p.name}: {type(e).__name__}: {e}")
                failed += 1
                continue
            if counts["assessed"]:
                p.write_text(out)
                files += 1
                assessed += counts["assessed"]
                for k, v in counts["labels"].items():
                    totals[k] = totals.get(k, 0) + v
            note = "" if pre else "  (record not found: quote stage only)"
            click.echo(
                f"  {p.name}: {counts['assessed']} assessed, {counts['skipped']} kept, "
                f"{counts['unlocated']} unlocated{note}"
            )
            click.echo(
                f"CHECK_JSON: {json.dumps({'digest': str(p), **_check_record(counts)})}"
            )
    finally:
        checker.release()
    click.echo(
        f"\n{files} digest(s) written, {assessed} claims assessed, {failed} failed"
    )
    for k in sorted(totals):
        click.echo(f"  {k}: {totals[k]}")
    if failed:
        raise SystemExit(1)
    if not assessed:
        raise SystemExit(3)


@main.command(name="salience")
@click.option(
    "--digests-root",
    type=click.Path(),
    default=str(_ANOMALICA / "digests"),
    help="Digests repo; every canonical digest carrying roles is read",
)
def salience_cmd(digests_root: str) -> None:
    """Report the reference roles across every digest that carries them.

    ACROSS DIGESTS, because one of the two instruments here is a corpus
    statistic and reading it inside a single record inverts it: the ambient
    prior says a term naming the whole field cannot be the subject of most
    claims, and on a record that is genuinely about unidentified objects it
    fired at 89% on nine edges that were right. It stays silent until a node's
    edges span enough records to have a population.

    The headline number is NOT an accuracy figure and the report says so. For
    accuracy, score against the hand-labelled set: reports/salience/hand-labels.yaml.
    """
    import yaml as _yaml

    from digester import salience

    digests, skipped = [], 0
    for f in sorted(Path(digests_root).glob("*.yaml")):
        d = _yaml.safe_load(f.read_text()) or {}
        claims = salience.claims_of(d)
        if any(r.get("role") for c in claims for r in salience._refs(c)):
            digests.append(d)
        elif claims:
            skipped += 1
    if not digests:
        click.echo(
            f"No digest under {digests_root} carries reference roles. "
            f"{skipped} carry claims without them - they predate the field."
        )
        raise SystemExit(1)

    rep = salience.report(digests)
    sole, first = rep["sole_reference"], rep["subject_first"]
    click.echo(f"{len(digests)} digests with roles, {skipped} without\n")
    click.echo(f"role mix: {rep['role_mix']}\n")
    click.echo(
        f"under-extraction (sole-reference claims whose one reference is not "
        f"the subject): {sole['under_nodded']}/{sole['assessed']} = "
        f"{sole['under_nodded_rate']}"
    )
    click.echo(
        "  Read as claims whose SUBJECT HAS NO NODE, not as role errors. Forty "
        "hand-labelled pairs found every one of these correct on that sample."
    )
    click.echo(
        f"\nsubject-first accuracy: {first['wrong']}/{first['subject_first_edges']} "
        f"wrong = {first['error_rate']}"
    )
    for name, r in sorted(rep["ambient"].items()):
        flag = "OVER CEILING" if r["over_ceiling"] else "ok"
        click.echo(
            f"\nambient {name}: subject on {r['subject_rate']:.0%} of "
            f"{r['edges']} edges across {r['records']} records - {flag}"
        )
    if not rep["ambient"]:
        click.echo(
            "\nambient check silent: no corpus-wide term yet spans enough "
            "records to have a population to be ambient across."
        )


@main.command(name="spend")
@click.option("--day", default=None, help="UTC day (YYYY-MM-DD); default today")
@click.option(
    "--provider-usd",
    type=float,
    default=None,
    help="What the provider says was spent. Without it, OPENROUTER_API_KEY is "
    "read and OpenRouter asked directly.",
)
def spend_cmd(day: str | None, provider_usd: float | None) -> None:
    """What the ledger accounts for on a day, against what was actually billed.

    The ledger gate cannot be enforced - anyone can post to a provider's URL
    without telling anyone - so this is the safety net: a gap means money was
    spent by something that wrote no row. That is the question nobody could
    answer on 2026-09-03, when a day's OpenRouter spend had no owner.

    Exit codes: 0 reconciled (or nothing to compare), 1 a gap over a cent.
    """
    import json as _json
    import os
    import urllib.request

    from anomalica_common.llm import ledger

    if provider_usd is None and os.environ.get("OPENROUTER_API_KEY"):
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as raw:
                data = _json.loads(raw.read().decode()).get("data") or {}
            # OpenRouter counts a UTC day, which is why the ledger does too.
            if day in (None, ledger.rows_for.__defaults__ and None):
                provider_usd = data.get("usage_daily")
        except OSError as e:
            click.echo(f"could not ask OpenRouter: {e}")

    out = ledger.reconcile(provider_usd, day)
    click.echo(f"day {out['day']} (UTC)")
    click.echo(
        f"  rows {out['rows']}, metered {out['metered_rows']}, unpriced {out['unpriced_rows']}"
    )
    click.echo(f"  ledger accounts for ${out['recorded_usd']:.4f}")
    for k in sorted(out["by_type"], key=lambda k: -out["by_type"][k]):
        click.echo(f"    {k:28} ${out['by_type'][k]:.4f}")
    if "provider_usd" not in out:
        click.echo(
            "  no provider figure: pass --provider-usd or export OPENROUTER_API_KEY"
        )
        return
    click.echo(f"  provider billed  ${out['provider_usd']:.4f}")
    if abs(out["gap_usd"]) <= 0.01:
        click.echo("  reconciled")
        return
    click.echo(f"  UNACCOUNTED      ${out['gap_usd']:.4f}")
    if out["unpriced_rows"]:
        click.echo(
            f"  ({out['unpriced_rows']} metered row(s) carry no cost, so part of the "
            "gap may be recorded work the provider priced elsewhere)"
        )
    click.echo(
        "  something spent money without writing a row; see anomalica_common.llm.probe"
    )
    raise SystemExit(1)


@main.command(name="accounts")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--model", default="sonnet", help="Model for the account pass")
@click.option(
    "--digest",
    type=click.Path(),
    default=None,
    help="An existing digest of this record; its claims are bound to the "
    "accounts and the floor applied. Without one the pass emits candidates "
    "only, since the floor cannot be checked without claims.",
)
@click.option("--out", type=click.Path(), default=None, help="Write the result as YAML")
@click.option("--min-claims", type=int, default=None, help="Floor (default 3)")
@click.pass_context
def accounts_cmd(
    ctx: click.Context,
    file_path: str,
    model: str,
    digest: str | None,
    out: str | None,
    min_claims: int | None,
) -> None:
    """Find the distinct accounts - the separate stories - in a record.

    Extraction pulls atomic facts and loses the shape of the source: three
    abductions described in one interview become one undifferentiated pile of
    claims. This marks where each telling runs, then binds the record's claims
    to them by span arithmetic, which is free because the claims are already
    located.

    An account is a SPAN OF THIS RECORD, never an entity - two sources telling
    the same story give two accounts, correctly, because there were two
    tellings.
    """
    import yaml as _yaml

    from anomalica_common.llm import ledger

    from digester import accounts as accounts_mod
    from digester.extract import (
        build_record_context,
        extract_accounts,
        provider_authority,
    )

    path = Path(file_path)
    parsed = parse_record(path.read_text(errors="replace"))

    use_api = resolve_use_api(_USE_API_VAR)
    input_authority = _authorise_hosted_input(path, model, use_api)
    if _uses_claude_allowance(model, use_api):
        allowance = check_allowance(
            session_headroom=headroom_for(len(parsed.body or "")),
            weekly_headroom=weekly_reserve_for(len(parsed.body or "")),
        )
        if not allowance.ok:
            click.echo(f"Allowance ceiling: {allowance.reason}")
            ctx.exit(77)
        click.echo(f"Allowance ok ({allowance.reason})")

    ledger.set_context(
        type="accounts",
        ref=(parsed.metadata.get("content_hash") or "").split(":")[-1] or None,
        source_type=parsed.source_type,
        body_chars=len(parsed.body or ""),
        source="digester-direct",
    )
    reset_usage()
    click.echo(f"Accounts: {parsed.title or path.name}")
    with provider_authority(input_authority, parsed.body):
        result = extract_accounts(
            parsed.body,
            model=model,
            record_context=build_record_context(
                parsed.title,
                parsed.creators,
                parsed.date,
                parsed.source_type,
            ),
            on_progress=click.echo,
            use_api=use_api,
        )
    candidates = result.get("accounts") or []
    click.echo(f"\n{len(candidates)} candidate account(s)")

    claims: list = []
    if digest:
        d = _yaml.safe_load(Path(digest).read_text()) or {}
        claims = (d.get("domain_claims") or []) + (d.get("infrastructure_claims") or [])
        click.echo(f"binding {len(claims)} claims from {Path(digest).name}")

    # The MATERIALISED body: the text the model saw and the text claim quotes
    # resolve against. parsed.body still carries the {{t:}} tokens that
    # materialise strips, so phrase-matching against it finds nothing.
    from anomalica_common.pre_digest import materialise

    body = materialise(parsed.body)
    # The record's own word timestamps map a claim's timecode straight to an
    # offset, with no quote matching and nothing lost. Built once per record.
    anchors = accounts_mod.build_time_map(parsed.body, body)
    if anchors:
        click.echo(f"word-timestamp map: {len(anchors)} anchors")
    bound = accounts_mod.bind(candidates, claims, body, anchors)
    kept, dropped = accounts_mod.apply_floor(
        candidates,
        bound["per_account"],
        min_claims if min_claims is not None else accounts_mod.MIN_CLAIMS,
    )
    click.echo(
        f"claims bound {bound['counts']['bound']}, outside any account "
        f"{bound['counts']['outside']}, unbindable {bound['counts']['unbindable']}"
    )
    click.echo(f"{len(kept)} account(s) clear the floor, {len(dropped)} dropped\n")

    by_index = {id(a): i for i, a in enumerate(candidates)}
    for a in kept:
        i = by_index[id(a)]
        click.echo(
            f"  [{bound['per_account'][i]:3} claims] {a.get('span_start')}-"
            f"{a.get('span_end')}  {a.get('title')}"
        )
        click.echo(
            f"        subject: {a.get('subject')} | when: {a.get('when') or '-'} | "
            f"where: {a.get('where') or '-'} | teller: {a.get('teller_role') or '-'}"
        )
    for a in dropped:
        click.echo(f"  DROPPED ({a['dropped_because']}): {a.get('title')}")

    if out:
        payload = {
            "record": {"content_hash": parsed.metadata.get("content_hash")},
            "model": model,
            "accounts": [accounts_mod.conform(a, by_index[id(a)]) for a in kept],
            "dropped": [
                {"title": a.get("title"), "because": a["dropped_because"]}
                for a in dropped
            ],
            "binding": bound["counts"],
        }
        Path(out).write_text(
            _yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        )
        click.echo(f"\nwrote {out}")
    click.echo(f"USAGE_JSON: {json.dumps(get_usage())}")


if __name__ == "__main__":
    main()
