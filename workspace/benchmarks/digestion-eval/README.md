# Digestion behaviour fixtures

These small synthetic narratives measure extraction behaviours without requiring
exact generated wording or exhaustive labels over a real corpus. They complement
the source-span evaluator in `digester/eval.py` and reuse the source-local account
and account-scoped chronology semantics in `account_chronology_eval.py`.

## Agent Experiment Workflow

Agents use the end-to-end runner rather than asking Mark to operate the scorer.
For every extraction-affecting change:

```bash
just fixture-eval
```

This creates valid synthetic record files, sends them through the production node
and claim passes with committed canned responses, writes only comparison variants
under `reports/digestion-eval/runs/`, scores them and compares them with
`stub-baseline.json`. That baseline is bound to the exact canned-response bytes and
is only a mechanical pipeline regression check. It makes no provider or local-model
calls and is not the model-quality baseline. The report starts with `better`,
`worse`, `unchanged` or `mixed`, then names failed behaviours in plain language.
Agents must inspect both `summary.txt` and the individual expectation evidence in
`report.json`.

A genuine extraction experiment uses the same command without canned responses:

```bash
just fixture-experiment --model MODEL
```

This uses the normal shared transport, input authority, allowance check, prompt
registry, production schemas, digest serialiser and variant writer. Metered routes
print one aggregate estimate and stop. After explicit approval for that amount,
the agent reruns with `--confirm`. Genuine runs compare with
`quality-baseline.json`, which represents accepted real model quality rather than
canned output. No fixture experiment writes a canonical digest.

Each run records the model, complete extraction configuration and fingerprint,
prompt hashes, Git revision and dirty state, fixture and prediction hashes, score,
baseline comparison, and any semantic-adjudication sidecar. A `better` result does
not update the baseline. After reviewing the complete evidence, an agent explicitly
accepts a genuine run with:

```bash
just fixture-accept-baseline reports/digestion-eval/runs/RUN/report.json
```

Acceptance rejects failed and stubbed runs, stale fixtures, missing or changed
variant artifacts, malformed scores and incomplete provenance. It recomputes the
score from the exact artifacts before writing `quality-baseline.json`. The baseline
contains the accepted report hash, run identity, model, effective configuration,
code revision, prediction hashes and full score, so ignored run directories are not
its only provenance. An old comparison outcome of `worse` or `mixed` does not block
deliberate acceptance; only the report's own validated evidence matters.

Run the included reference prediction from `workspace/`:

```bash
python -m digester.cli eval-fixtures benchmarks/digestion-eval/cases.yaml \
  --variant reference=benchmarks/digestion-eval/reference-set.yaml
```

Compare variants by repeating `--variant NAME=PATH`. Add `--json-out report.json`
for complete machine-readable evidence, or `--case CASE-ID` to isolate one case.
The scoring command only reads files and never calls a model.

Each source has its own digest prediction. For a fixture file with several cases,
the variant path is a manifest that maps case ids to relative digest paths:

```yaml
schema: anomalica/digestion-evaluation-predictions/1
predictions:
  ridge-rescue-reverse-narration: predictions/ridge-rescue.yaml
  archive-restoration-range: predictions/archive-restoration.yaml
```

With `--case`, the variant path may instead be that case's digest directly.

## Semantic Adjudication

Declared terms are always the deterministic first pass. When a valid paraphrase
cannot be represented reliably with more term alternatives, an independent human
or model judge may provide a semantic-adjudication sidecar:

```bash
python -m digester.cli eval-fixtures benchmarks/digestion-eval/cases.yaml \
  --case ridge-rescue-reverse-narration \
  --variant candidate=predictions/candidate.yaml \
  --adjudication candidate=predictions/candidate.adjudication.yaml
```

`--adjudication NAME=PATH` attaches the sidecar to the variant with the same
name. Variants without one use deterministic matching only. A sidecar has this
shape:

```yaml
schema: anomalica/digestion-evaluation-adjudications/1
fixture_sha256: sha256:<hash-of-exact-fixture-bytes>
prediction_sha256:
  ridge-rescue-reverse-narration: sha256:<hash-of-exact-digest-bytes>
judge:
  kind: model # or human
  identifier: provider/model-or-human-identifier
metadata:
  experiment: paraphrase-review-1
decisions:
  - case: ridge-rescue-reverse-narration
    concept: rescue-end
    claim_ids: [claim-17]
    decision: equivalent # equivalent, not_equivalent, or abstain
    rationale: The claim expresses completion of the same rescue.
```

Every case, concept and claim id is validated. The fixture hash and each digest
hash must match the exact files being scored; stale, incomplete or unknown
bindings fail closed. Only `equivalent` augments concept coverage and downstream
context, date, reference, account and chronology checks. Negative and abstaining
decisions remain visible in text and JSON evidence.

A future model runner may produce these sidecars using the shared model transport,
operation ledger and normal allowance or metered-spend approval controls. Model
inference does not belong inside this scoring command: keeping judgement separate
makes decisions reviewable, cacheable and safely invalidated by input hashes.

## Add A Case

Add one item to `cases` with a realistic `source` passage and these independent
expectation lists:

- `concepts`: each `terms` entry is required; a nested list gives accepted
  alternatives. Matching is case- and punctuation-insensitive.
- `contexts`: concepts that must share one predicted account, with a referent
  named somewhere in that grouped context. A claim may naturally use a pronoun.
- `dates`: accepted `date` strings or two-item `date_range` lists.
- `references`: required entity, topic, or event reference names on a matching
  claim. The kind labels the expectation; matching uses digest `refs` names.
- `accounts`: concepts that must be grouped in one source-local account.
- `chronology`: `before`, `simultaneous`, or `unknown` relationships between two
  concepts in that account.

Predictions are ordinary digest YAML or JSON plus a harness-only additive block:

```yaml
evaluation:
  accounts:
    - id: source-local-telling
      claim_ids: [claim-a, claim-b]
  chronology:
    - account_id: source-local-telling
      before: [[claim-a, claim-b]]
      simultaneous: []
      unknown: []
```

An account is one telling in this source. An event reference is only a
source-local candidate name here; this harness does not perform or claim
cross-record global event resolution.

The deterministic matcher is deliberately transparent and suitable for continuous
integration. It recognises only terms and alternatives written into the fixture.
Use a focused alternative for stable lexical variation and an explicit sidecar
when the remaining decision is genuinely semantic; reports distinguish the two.
