# Digester Agent Guide

Parent Product and root Core instructions are loaded through `opencode.json` and remain
mandatory.

## Component Boundary

The digester reads reviewed ingest records and performs per-record extraction.
It writes one YAML digest containing nodes, atomic claims, and provenance. It
does not build or maintain the cross-record graph; that belongs to the
assimilator.

Canonical component and interchange contracts live in the sibling architecture
repository:

- `../anomalica/architecture/digester.md`
- `../anomalica/architecture/digest-format.md`
- `../anomalica/architecture/node-types.md`
- `../anomalica/reference/format-specs.yaml`
- `../anomalica/architecture/model-policy.yaml`

Use `anomalica_common.digest` for digest serialization and
`anomalica_common.llm` for model transport. Do not create component-local copies
of shared schemas, model policy, or transport logic.

## Repository Layout

- `workspace/digester/`: Python package and CLI.
- `workspace/tests/`: deterministic test suite.
- `workspace/benchmarks/`: extraction evaluation tools and fixtures.
- `reports/`: experiment reports and local run artefacts.
- `cm.yaml`: container definition; generated container scripts must not be
  edited directly.

## Development

Build the development image after dependency changes:

```bash
cm build
```

Run the component suite:

```bash
just test
```

Run a focused test inside the existing development image when iterating:

```bash
docker run --rm \
  -v "$(pwd)/workspace:/home/nonroot/workspace" \
  -v "$HOME/repos/anomalica/product/anomalica-common/src:/opt/anomalica-common:ro" \
  --user "$(id -u):$(id -g)" \
  -w /home/nonroot/workspace \
  anomalica-digester:development \
  python -m pytest tests/test_extract.py -q
```

Use `PYTHONPATH="$HOME/repos/anomalica/product/anomalica-common/src:workspace"` for
host-side deterministic CLI commands such as `just health`.

## Extraction Safety

- Model-backed extraction consumes a shared subscription allowance or metered
  API funds. Determine the route and obey the workspace approval and spend
  gates before starting it.
- Run `python -m digester.cli selftest <real-record>` before a paid batch so a
  serialization failure cannot discard completed model work.
- Experiments must use the variant layout and must not overwrite canonical
  digests. Keep the prompt, schema, code revision, model, effort, and cache state
  pinned or recorded so comparisons remain interpretable.
- Tests must stub model calls. Unit and integration tests must remain
  deterministic and free of subscription or API spend.
- Do not edit sibling ingest, digest, architecture, or shared-library
  repositories unless the task explicitly includes them. Report cross-component
  defects to the owning workspace.

## Extraction Experiments

- Any change that can affect extracted nodes or claims must run `just fixture-eval`
  before it is considered complete. This runs the production nodes and claims
  passes with canned transport responses, writes comparison-only variants, and
  compares with `stub-baseline.json` without calling any model. This is a
  mechanical pipeline regression gate, not a measure of model quality.
- Read the generated `summary.txt` and `report.json`, including every failed
  behaviour. A green aggregate is not enough when an individual expectation
  failed.
- Run a genuine fixture experiment with `just fixture-experiment --model MODEL`.
  Genuine runs compare with `quality-baseline.json`; they never use the canned
  response baseline.
  For a metered route, first run without `--confirm` to print the aggregate cost;
  obtain explicit approval for that amount, then rerun with `--confirm`.
- Experiment output belongs under `reports/digestion-eval/runs/`. It is always
  variant-only and must never update canonical digests.
- Never accept a baseline automatically because a run reports `better`. After
  inspecting its individual evidence, explicitly run
  `just fixture-accept-baseline reports/digestion-eval/runs/RUN/report.json`.
  The command accepts only a complete non-stub report for the exact current
  fixture, rehashes its variant artifacts, recomputes its score, and records the
  report, model, configuration and code provenance in `quality-baseline.json`.
  Its original comparison outcome does not control acceptance.
- Do not edit `stub-baseline.json` to hide a mechanical regression or replace
  `quality-baseline.json` merely to make a quality regression disappear. Baseline
  acceptance must represent an intentionally reviewed improvement or changed
  requirement.
