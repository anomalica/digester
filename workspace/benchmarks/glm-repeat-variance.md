# Run-to-run variance: GLM-5.2 repeated on jon-stewart

Two runs of the **same model, same record, same prompt sha (d161b1ed), same
schema enforcement (mixed)**. This is the repeat measurement the model
comparison had been deferring, obtained as a by-product of the streaming
re-run rather than bought deliberately.

Record: `2026-01-02-video-the-alien-interview-tape-might-be-real-jon-stewart.v2`
Gold: the record's in-body highlight spans.

| | run 1 (2026-07-28) | run 2 (2026-07-29) | delta |
|---|---|---|---|
| claims | 626 | 663 | +37 |
| recall | 63.7 | 61.7 | **-2.0** |
| quote fidelity | 92.3 | 94.6 | **+2.3** |
| coref-mech | 67/78 (85.9) | 72/82 (87.8) | **+1.9** |
| off-target | 43.9 | 49.9 | **+6.0** |
| broken quotes | 48 | 36 | -12 |
| stamped input tokens | 154,041 | 155,012 | +971 |
| stamped output tokens | 260,889 | 289,599 | +28,710 |
| billed | $2.60 | $2.04 | -$0.56 |
| attempts | 89 over two days | 1 failure, one container ~2h | |

## What it establishes

**Run-to-run variance on this instrument is ~2 points of recall and ~2 points
of coreference.** Every gap between the top three models on the same record is
the same order:

| model | recall |
|---|---|
| opus | 64.0 |
| glm-5.2 | 61.7 - 63.7 (two runs) |
| sonnet | 60.9 |

The opus-to-sonnet gap is 3.1 points against 2.0 points of measured
single-model variance. **The top three are not separated by this instrument at
one run each.** Only the haiku gap (43.3) exceeds the noise.

Off-target moved 6.0 points between identical configurations, so that axis is
noisier still and should not be read comparatively at N=1 at all.

## Caveats

- **Not a pure repeat.** Run 2 added response streaming to the transport. The
  request body is otherwise identical (`stream: true` only), so this is a
  transport change rather than a prompt or sampling change, but it is not
  nothing.
- **Enforcement was `mixed` in both runs**, and the native/prompt split per
  call is not recorded, so it may itself differ between the two and contribute
  to the deltas.
- **N=2.** Two runs bound variance loosely. The deltas above are one sample of
  the difference, not a standard deviation.

## How this data nearly did not exist

Run 2 **overwrote** run 1's variant file - `digests/variants/.../z-ai-glm-5.2.d161b1ed.yaml`
is a single path keyed on (model, prompt sha), and the `digests` variants
directory is untracked, so there was no git history to recover from. Run 1's
numbers survive only because they had been graded and reported before the
overwrite.

`--run-label` exists precisely to prevent this (digester b029027, "so a
deliberate repeat cannot overwrite its twin"). It was not used, because the
second run was framed as a re-run of a failure rather than as a repeat. Any
run of an already-measured configuration is a repeat, whatever its motivation,
and needs a label.
