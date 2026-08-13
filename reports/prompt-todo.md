# Pending prompt changes

Held deliberately, not forgotten. Each bumps the prompt sha, which marks every
prior digest stale and breaks comparability across the boundary - so they land
between batches, never during one.

## Quote verbatimness: no interpolated speaker labels

A quote must be the source's words only. Speaker attribution belongs in the
`speaker` field, which already exists and is populated, so keeping the quoted
text clean loses no information.

Found 2026-08-01 by anomalica/master, checking the claims that FAIL verification
rather than sampling ones that pass. Scale: 27,966 claims corpus-wide, 32 whose
quote opens with a speaker label, and exactly ONE where the label is confirmed
absent from the source - the other 31 are genuine, because the source itself is
in that form, as transcripts are.

So this is a single instance, not a class, and it does not justify a re-run. It
matters anyway: for a project whose proposition is verifiable citation, adding
words inside quotation marks is the one thing a quote may not do, however
helpful the addition.

WHY IT IS NOT LANDING TODAY: 14 of the 16 provenance re-digests are still
running under v4/v4. Changing the prompt mid-batch would split that batch across
two shas and make the before/after unusable - the same comparability trap the
version-bump discipline exists to prevent. Land it once the batch completes.
