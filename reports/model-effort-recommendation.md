# Which model extracts, and at what effort

Recommendation, evidence, and an explicit line between what the measurements
establish and what they merely fail to disprove.

**Recommendation: Sonnet 5 at low effort for the digest stage. Opus 5 where
completeness matters more than cost and a record gets one pass. Haiku 4.5 not
for long records at all, and on short ones only behind the low-yield guard with
an automatic re-run.**

Superseded the 2026-09-03 grid entirely. That one mixed two claims prompts, was
graded before the coverage metric was fixed, and ran while the schema handed the
model its enum options in a randomised order. This one is 14 cells at a single
pinned configuration - prompt `0f2d8dc9`, schema `4f4332da`, code `dccf3da6` -
with the call cache off, three arms per model per record where the allowance
allowed.

## The grid

Raymond Fowler interview, 10,001 words, 3 chunks, 69 gold units:

| model | recall | fidelity | claims | output tokens/claim |
|---|---|---|---|---|
| Opus 5 | 0.909 / 0.888 / 0.872 → **0.890** | 0.969 / 0.969 / 0.965 | 323 / 323 / 313 | 371 |
| Sonnet 5 | 0.804 / 0.858 / 0.869 → **0.843** | 0.977 / 0.979 / 0.974 | 221 / 242 / 227 | 525 |
| Haiku 4.5 | 0.501 (one arm) | 0.956 | 135 | 1,516 |

Ross Coulthart Skywatcher session, 4,609 words, 1 chunk, 43 gold units:

| model | recall | fidelity | claims |
|---|---|---|---|
| Opus 5 | 0.941 (one arm) | 0.986 | 143 |
| Sonnet 5 | 0.889 / 0.893 / 0.899 → **0.894** | 1.000 / 1.000 / 1.000 | 104 / 119 / 117 |
| Haiku 4.5 | 0.183 / 0.936 / 0.880 → 0.666 | 1.000 / 0.992 / 1.000 | 20 / 122 / 91 |

## The floor is a property of the record, not the corpus

This is the methodological result and it changes how every other number here is
read. Sonnet, identical arms, cache off:

| record | chunks | spread across 3 arms |
|---|---|---|
| Fowler, 10,001 words | 3 | **6.5 points** |
| Skywatcher, 4,609 words | 1 | **1.0 point** |

Sixfold, same model, same configuration. The variance lives at the chunk
boundaries, where runs diverge in what the accumulated node directory carries
forward. Every "3.9-point noise floor" statement in these notes is really a
statement about multi-chunk records, and it was a three-sample underestimate
even for those.

The practical consequence inverts an earlier note. A short single-chunk record
is not merely cheaper to compare on, it is QUIETER: on Skywatcher a 1-point
difference separates, on Fowler nothing under about 7 points can.

Removing the randomised enum order did NOT reduce run-to-run variance. It
removed an uncontrolled variable from the experiment without making it quieter.

## Established

**Opus recalls about 4.7 points more than Sonnet.** +4.60 on the long record,
+4.73 on the short one - the same sign on both records, near-identical
magnitudes, and on each record every Opus arm scored above every Sonnet arm.
That is the two-record criterion this project set, and it is met. Note the
strength available: with three arms against three, an exact permutation test
bottoms out at p = 0.050, which is what complete separation yields and the most
that sample size can show.

**Sonnet grounds its quotes better than Opus, on both records, also with
complete separation.** -0.90 points on the long record, -1.40 on the short,
where Sonnet's three arms all located every quote (1.000) and Opus did not.
Checked against the known defects in the fidelity measure before believing it:
Opus does write more ellipsis-elided quotes (7, 2, 8 against 2, 6, 3), and
correcting for elision and Unicode punctuation leaves the gap unchanged at
0.9655 against 0.9753. It is grounding, not measurement.

**Haiku is far below both on a long record.** 0.501 against Sonnet's 0.843 -
34 points, five times that record's floor. One arm, so not established by the
two-record rule, but the margin is not close and the supporting evidence is
mechanical: it took 3,351 seconds against Sonnet's ~1,000 and spent 204,726
output tokens producing 135 claims where Sonnet spent 116,074 producing 221.

**Haiku throws a catastrophic run roughly one time in three on a short record.**
0.183, 0.936, 0.880 - a 75-point spread. Its central case matches Sonnet; the
failure produced 20 claims where the record yields about 110, and burned 148,797
output tokens doing it. `health.low_yield` catches that run (0.88 claims/KB
against the good arms' 5.34 and 3.98) and flags nothing else in the corpus, so
the failure is detectable rather than silent - but it needs the guard and an
automatic re-run, and Sonnet needs neither.

**Effort medium is not better anywhere** (2026-09-03 grid, superseded for
magnitudes but not for direction): below low on both records and better on
neither, at higher cost.

**Luna sits 15 to 26 points below the field** (2026-09-03 grid). Four to seven
times any floor measured here, same direction on both records. It should not
extract.

## Not established

**That Opus is the better extractor overall.** The two established results point
opposite ways: it finds more and grounds worse. Per record it produces about
five more quotes that cannot be located in the source than Sonnet does (10.3
against 5.4 on the long record), because it writes 40% more claims at a slightly
lower grounding rate.

**Anything about DeepSeek or Luna at this configuration.** Both are metered and
were excluded; their figures above come from the superseded grid.

**Haiku on a long record, and Opus on a short one**, each having one arm.

## Why Sonnet is the default despite Opus recalling more

The asymmetry between the two failure modes decides it. A missed claim is an
absence - recoverable by a later pass, another record, or a reviewer. A claim
whose quote cannot be located in the source is an assertion that LOOKS sourced
and is not, and it propagates to every page built from that claim. The project's
own standard is that accuracy matters more here than anywhere else because
nothing downstream can catch it.

Sonnet is also stable where it matters (1.0-point spread on a single-chunk
record, 6.5 on a chunked one, no failure mode observed in six arms), and Opus
burns the plan roughly twice as fast.

Opus earns its place where a record gets one pass and completeness is worth more
than the cost - but its output needs the fidelity check applied, not assumed.

## What the artefacts carry

Every cell above records `extraction_config` in its body with prompt, schema and
code fingerprints, and the variant filename carries the prompt hash.
`grade-record` prints both columns and refuses to let a mixed table pass
silently. Reproduce with `reports/run-grid.sh`; analyse with
`reports/grid_analysis.py`, which reports the floor, an exact permutation p, and
whether the arms separate.

One known gap: the code fingerprint walks a hand-enumerated set of directories
and misses `anomalica_common/irrelevant.py`, which decides how much of a record
reaches the model. It has not changed under a run, but the guarantee is narrower
than the field advertises.

## Related

- `anomalica/knowledge/reading-a-model-comparison.md`
- `anomalica/knowledge/pick-the-densest-signal-not-the-biggest.md`
- `anomalica/knowledge/the-fingerprint-that-differed-from-itself.md`
- `reports/quote-location-audit.md` - why every fidelity figure is a lower bound
