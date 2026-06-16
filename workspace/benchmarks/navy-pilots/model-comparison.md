# Model comparison: opus vs sonnet vs haiku (navy record)

Date: 2026-06-16
Transport: Claude subscription (DIGESTER_USE_API=0), no API dollars.
Method: same production two-pass prompt (NODES_PROMPT_V2, no per-model override)
on `2021-05-17 Navy pilots describe encounters with UFOs`, so the model is the
only variable. Node recall/precision graded deterministically against
golden.yaml; claim recall via the (subscription) LLM judge against the navy
ground truth; claim quality via the assess.py heuristic flags. Reproduce with
`python benchmarks/model_compare.py`.

| model  | wall s | node recall | node precision | claims | claim recall | quality flags |
|--------|-------:|------------:|---------------:|-------:|-------------:|---------------|
| haiku  |    541 |        0.87 |           1.00 |    118 | 0.98 (missed mellon_leak) | 1 rptAnc, 2 vague |
| sonnet |    223 |        0.87 |           1.00 |     87 | **1.00**     | 1 rptAnc, 2 vague |
| opus   |  **171** |      **0.97** |         1.00 |     66 | 0.98 (missed mellon_leak) | 1 cmpd, 2 vague |

## Reading

- **Node recall**: opus leads decisively (0.97 vs 0.87). This is the hardest,
  most-differentiated metric - opus captures ~10% more of the golden entities.
- **Claim recall**: effectively tied - sonnet 1.00, opus and haiku 0.98 (both
  missed the same single fact, `mellon_leak`).
- **Speed**: opus fastest (171s), sonnet 223s, haiku slowest by far (541s, 3.2x
  opus). Matters now that the subscription's shared fleet rate-limits, not
  dollars, are the constraint - a slow model saturates the fleet longer per
  record.
- **Claim count is granularity, not noise.** Quality flags are uniformly low
  across all three (no imperial, no American spelling, near-zero compound/vague),
  so haiku's 118 claims are not padded with junk - it splits finer; opus
  consolidates (66). Neither is clearly better; both capture the must-facts.

## Recommendation: standardise on Opus

With cost off the table (subscription), haiku's only advantage - price - is gone,
and it is now the worst option: tied-lowest node recall and 3.2x the wall-clock
(so 3.2x the rate-limit contention) for no quality gain. Drop it.

Between opus and sonnet, opus wins the dominant metrics: decisively higher node
recall (0.97 vs 0.87) and fastest wall-clock, at perfect precision and clean
claims. Sonnet's edge is one fact of claim recall (1.00 vs 0.98). Node recall is
the harder bar and opus clears it best; the one-fact claim gap (`mellon_leak`) is
likely closeable with a small prompt note. Sonnet is the close alternative if
claim recall is weighted above node recall.

## Caveats

- One record (navy), default prompt - no per-model tuning. Opus led without a
  tuned prompt, so it likely has headroom.
- `mellon_leak` was missed by both opus and haiku (sonnet caught it). Worth
  checking whether it is a systematic class of fact before tuning.
- If downstream (assembler / evidence-scoring) prefers finer-grained atomic
  claims, that is a mild point toward more-granular output - but haiku's finer
  split came with worse node recall and 3.2x the time.
