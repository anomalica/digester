# Blocked from the book queue

## passportmagonia - UNBLOCKED 2026-08-01

Fixed in anomalica-common: the prompt is delivered on stdin instead of argv.

**The original diagnosis named the wrong argument.** It attributed the failure to
the claims schema embedding the locked node enum. Measured against real node-name
lengths from the corpus (11,437 names, mean 26.7 characters), at 3,017 nodes:

    node directory inside the PROMPT   143KB   <- over the limit, this was it
    claims schema with the node enum    91KB   <- always fitted

Linux caps a SINGLE argv element at MAX_ARG_STRLEN (32 pages = 131,072 bytes),
separately from the ~2MB ARG_MAX total. Both the document chunk and the node
directory rode in one `-p <prompt>` argument, so the prompt blew the per-argument
cap while the schema - the thing that got blamed - sat comfortably under it.

    OSError: [Errno 7] Argument list too long: 'claude'

E2BIG names no argument, so the largest plausible culprit was assumed. Verified
both directions: a 200KB payload raises E2BIG via argv and succeeds via stdin,
with schema enforcement and prompt caching intact.

`--json-schema` has no file form, so the schema necessarily stays in argv and
keeps the ceiling. It is now checked explicitly with a message naming the cause,
rather than surfacing as a bare E2BIG. Headroom is ~37KB, about 1,278 more nodes
before a catalogue would trip it at ~4,300.

This was a ceiling on record COMPLEXITY, not size - In Plain Sight at 778KB is
larger and runs fine - so it silently excluded catalogue-shaped sources as a
class, not just this one book.

Note the book has not been re-run yet: weekly allowance sits at 92% against a 93
start-line, so the queue parks. It runs when the window rolls.

The source itself is FINE. It was excluded for a week on "47 U+FFFD replacement
characters"; those are 0.006% of the text, one per 16,328 characters, and every
one sits where an em-dash belongs. That exclusion was wrong and is withdrawn.


## hair-of-the-alien - UNBLOCKED 2026-07-31

Never a bad record and never over-marked. `parse_record` treated each of the
book's 12 bare `---` lines as an annotation fence and discarded the prose between
pairs of them: a 620KB body parsed to 88KB, the pre-digest reduced that to 1KB,
and extraction produced 9 claims from a full-length book - exit 0, digest written,
no warning of any kind.

Fixed in digester 2c68c73: a fence must be corroborated by annotation content
before anything is dropped. The book parses 100% of its body now and is back in
the queue at its size position.
