# Which model extracts, and at what effort

Recommendation, evidence, and an explicit line between what the measurements
establish and what they merely fail to disprove.

**Recommendation: Sonnet 5 at low effort for the digest stage. Opus only where
a record has failed on Sonnet.**

## The grid

Two records, both chosen for dense reviewer highlights rather than length, both
graded on the fixed coverage metric (a highlighted character counts once,
however many claims quote it). All rows below share prompt fingerprint
`e9b8b6d4`; rows at any other fingerprint are excluded, because a prompt change
is a variable exactly like the model.

Raymond Fowler interview, 10,001 words, 69 gold units:

| model | recall | fidelity | claims |
|---|---|---|---|
| Opus 5 | 0.892 | 0.976 | 335 |
| DeepSeek v4 Pro | 0.889 | 0.985 | 203 |
| Sonnet 5 | 0.865 / 0.834 / 0.831 / 0.826 | 0.988 / 0.978 / 0.991 / 0.992 | 257 / 227 / 224 / 242 |
| Sonnet 5, effort medium | 0.797 | 0.986 | 286 |
| GPT-5.6 Luna | 0.685 | 0.993 | 273 |
| Haiku 4.5 | not run at this prompt | | |

Ross Coulthart Skywatcher session, 4,609 words, 43 gold units:

| model | recall | fidelity | claims |
|---|---|---|---|
| Opus 5 | 0.931 | 0.986 | 151 |
| Sonnet 5 | 0.913 | 1.000 | 127 |
| Sonnet 5, effort medium | 0.892 | 0.991 | 108 |
| DeepSeek v4 Pro | 0.859 | 0.981 | 107 |
| Haiku 4.5 | 0.842 | 1.000 | 109 |
| GPT-5.6 Luna | 0.649 | 0.989 | 89 |

The four Sonnet arms on Fowler are identical runs with the call cache off. They
spread 3.9 points, and three samples understate a spread, so **3.9 points is a
lower bound on the noise floor**. Any gap at or below it means nothing.

## Established

**Luna is far below the field, on both records, by margins nothing else here
approaches**: 15.4 points below the Sonnet mean on Fowler and 26.4 on
Skywatcher. Four to seven times the floor, same direction on both records. It
should not extract.

Nothing else in this grid is established. What follows is the specific reason
in each case.

## Not established

**Opus over Sonnet.** +5.3 points on Fowler, +1.8 on Skywatcher. Consistent in
direction, but one gap sits inside the floor and the other barely outside it on
a floor that is itself a lower bound. Opus also produced 33% more claims on
Fowler (335 against 224-257) and 19% more on Skywatcher for that gap, so the
extra output is not buying measured coverage. It is the most expensive model on
the plan.

**DeepSeek against Sonnet - the direction flips.** +5.0 on Fowler, -5.4 on
Skywatcher. Two records, opposite signs, both around the floor. Worth noting
that on Fowler it matched Opus (0.889 against 0.892) on 203 claims against
Opus's 335.

**Haiku below Sonnet.** 7.1 points down on Skywatcher, which is outside the
floor - but that is ONE record, and the rule here is that no recall difference
measured on one record counts. Haiku was never run on Fowler at this prompt.
The run made on 2026-09-08 used a different claims prompt and cannot be added
to this table; it scored 0.770 against a Sonnet mean of 0.839 at the older
prompt, which points the same way and proves nothing, because the prompt
differs.

**A live policy line rests on the untested half of this.** The digest stage
rationale in `anomalica/architecture/model-policy.yaml` says short single-chunk
sources go to Haiku, "whose recall holds". Skywatcher is single-chunk and
Haiku's recall did not hold there: 7.1 points below Sonnet and 8.9 below Opus,
both outside the floor. One record is not a finding, but the policy line has no
measurement behind it at all, and the one measurement that exists contradicts
it. It should not stand unqualified.

## No evidence of benefit

**Effort medium.** Below low on both records: -4.2 points on Fowler against the
Sonnet mean, -2.1 on Skywatcher. Each gap is at or inside the floor, so medium
is not measurably WORSE. It is also not better anywhere, on either record, and
it costs more. There is no case for it.

## The gap, and what closing it costs

The grid is missing Haiku on Fowler at `e9b8b6d4`, and that cell cannot be
made. The variant filename hashes the PROMPTS only; the claims schema and the
extraction code have both changed since these artefacts were written, so
pinning the old prompts would produce a file that shares the fingerprint and
differs underneath - the same confound, hidden better.

Closing it honestly means re-running the whole grid at the current
configuration: five models across two records, plus repeat arms to re-measure
the floor, since the floor is configuration-specific too. That is roughly a
dozen runs on the subscription and it is a quota decision, not a technical one.

The cheaper question first: what would it change? Luna is out either way. The
Sonnet recommendation stands on Opus's advantage being unproven and Haiku's
disadvantage being pointed-at-but-unproven, and a full re-run would resolve
both. If either resolves the other way, the recommendation moves.

## What the artefacts carry

Every row in the tables above was written before the extraction fingerprint
existed, so their `extraction_config` is empty and the prompt sha in the
filename is the only configuration they record. Runs from 2026-09-08 onward
carry `extraction_config` with prompt, schema and code fingerprints in the body.
`grade-record` prints both columns and refuses to let a mixed table pass
silently.

## Related

- `anomalica/knowledge/reading-a-model-comparison.md` - the noise floor and the
  one-record rule.
- `anomalica/knowledge/pick-the-densest-signal-not-the-biggest.md` - why these
  two records and not the two longest.
