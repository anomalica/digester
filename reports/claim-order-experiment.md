# Ordering claims within an account

Where claim ordering belongs in the pipeline, which model does it, and how well.
Two accounts from the Richard Doty interview, hand-labelled against the transcript.

## Result

**A separate pass, after claims, scoped to one account. Sonnet. Model pairs
combined with narration order.** 187 of 190 orderings correct on the hard
account and 36 of 36 on the easy one, with no wrong assertions on either.

## Why not inside the claims pass

The claims pass chunks at 20,000 characters. The Doty record materialises to
217,109, so 11 chunks. The account tested here - the repeatedly abducted Air
Force sergeant - has its two halves at character 113,834 and 121,674, which is
chunk 5 and chunk 6.

The speaker tells the end of that account first, then says "let me go back a
little bit" and tells the beginning. **The two halves are never in the same
call**, so a pass that orders within a chunk cannot see the one relationship
that needs ordering. This is not an argument about quality; the ordering signal
is structurally outside a chunk's view.

Joint extraction was tested anyway on a single passage: 39 claims against 40 for
claims alone, same average length, no visible degradation. So the objection is
chunk scope, not claim quality.

## The method

The model returns pairs, never a sequence:

    before: <claim id> <claim id>

A missing pair means "not known", never "same time". Uncertainty needs no
representation - it is the absence of an assertion.

Combining with narration order, which is free and already recorded on 99.99% of
claims as `location_in_record`:

1. Take the model's pairs and compute the transitive closure.
2. For each claim, count what fraction of its model pairs run against narration
   order. A claim above half, with at least three pairs, is **displaced** - the
   speaker moved it out of sequence.
3. Fill every remaining pair from narration order, skipping any pair that
   touches a displaced claim.

Step 2 is what makes the combination safe. Without it the fill reintroduces the
inversions the model had corrected.

## Measurements

Pairwise agreement with hand truth. "Inverted" is an assertion in the wrong
direction; "unstated" is a pair no method placed.

Account one - the abducted sergeant, 20 ordered claims, 190 pairs. The speaker
tells the climax first.

| method | correct | inverted | unstated |
|---|---|---|---|
| narration order alone | 171 | **19** | 0 |
| sonnet alone | 136 | 0 | 54 |
| haiku alone | 123 | 0 | 67 |
| opus alone | 187 | 0 | 3 |
| haiku + narration | 183 | 0 | 7 |
| **sonnet + narration** | **187** | **0** | 3 |
| opus + narration | 187 | 0 | 3 |

All 19 inversions in the first row are one claim: the surveillance operation,
told first and happening last. Every model detected it as displaced.

Account two - the Sandia guard, 9 claims, 36 pairs. Told in order throughout.

| method | correct | inverted | unstated |
|---|---|---|---|
| narration order alone | 36 | 0 | 0 |
| haiku alone | 34 | **2** | 0 |
| haiku + narration | 34 | **2** | 0 |
| sonnet alone | 28 | 0 | 8 |
| **sonnet + narration** | **36** | **0** | 0 |

Haiku placed the entities emerging before the guard fetched his shotgun. The
transcript has it the other way. One account was not enough to see this - haiku
was clean on the first.

## What each part contributes

Narration order is high recall and makes confident errors, all of them at the
points where a speaker jumps. The model is lower recall and, on Sonnet and Opus,
made no error on either account. The combination takes the model's judgement
where it has one and narration order everywhere else.

## Output conventions differ between models and must be normalised

Sonnet returned 171 pairs, Opus 20, Haiku 21, for the same task. Opus and Haiku
emit the consecutive chain; Sonnet emits the closure. Scored raw, Opus looks
eight times worse than Sonnet; scored after closure it is equal or better.
Always close before comparing.

## Not established

Two accounts, one record, one run each. Nothing here separates Sonnet from Opus,
and the noise floor for this task has not been measured. What is established is
that Haiku errs where Sonnet and Opus did not, and that narration order alone
fails badly on an account whose telling is out of sequence.
