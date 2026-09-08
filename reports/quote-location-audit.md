# Why 11,230 claim quotes do not locate in their record

Audited 2026-09-08 over all 151 canonical digests: 42,210 claims carry a quote
and 11,230 of them (26.6%) cannot be found in the record they came from. That
number has been read as a fidelity result. Most of it is not.

## The split

Each unlocatable quote was retested twice: once with Unicode punctuation
flattened on both sides, and once split on ellipsis with each segment located
separately.

| source | quotes | unlocated | punctuation only | elided | left |
|---|---|---|---|---|---|
| ebook | 23,397 | 7,906 | 1,940 | 678 | 5,288 |
| video | 13,745 | 2,503 | 0 | 1,587 | 916 |
| web | 1,734 | 586 | 225 | 10 | 351 |
| pdf | 2,740 | 188 | 3 | 46 | 139 |
| audio | 582 | 47 | 0 | 32 | 15 |
| **all** | **42,210** | **11,230** | **2,168** | **2,353** | **6,709** |

**2,168 are the grader's own punctuation handling.** The quote is verbatim; a
curly apostrophe, a typographic dash or a non-breaking space differs from the
record's. `searchable()` normalises whitespace and case and stops there.

**2,353 are elided quotes** - the model joined non-contiguous passages with an
ellipsis, and every segment appears verbatim. Standard quotation practice, and
the grader has no concept for it, so it scores them as broken.

Together that is 40% of the apparent failures, and they are not the model's.

## The video figure is not what it looks like

Video and audio carry no punctuation failures at all and their remaining
unlocatable quotes are dominated by elision. A first pass at this audit read
the raw record body and found 13,741 of 13,745 video quotes unlocatable, which
looked like a catastrophic finding and was an error in the audit: record/2
bodies carry inline `{{t:SECONDS}}` word timestamps, so nothing matches until
the body is materialised. `grade_digest` materialises; the audit had not.

## Two books are marked almost entirely irrelevant

Materialising also strips reviewer-marked `<!-- irrelevant: start -->` regions,
and on two records those markers cover the book rather than its front matter:

| record | dropped | largest single region |
|---|---|---|
| 2023-11-17-ebook-in-plain-sight | 84% of 764,401 chars | 633,388 |
| 2014-09-27-ebook-the-invisible-college | 97% of 394,554 chars | 368,483 |

In `in-plain-sight` the marker opens at line 162, mid-introduction, directly
after "Annie knows her story sounds implausible, but she's adamant it's true",
and the matching close is at line 2406, after the acknowledgements. Every
chapter is inside it. The bibliography and index that follow are NOT marked -
the index carries a closing marker with no opening one, so the pairing is
shifted by one marker.

The existing digests were built from the full text: `in-plain-sight` has 2,817
claims, proportionate to 764,401 characters and not to the 121,450 that survive
the strip. So the markers post-date those digests, or were never applied to
them. The damage is prospective and it is total - **re-extract either book
today and the model sees 3% and 16% of it.**

This is reviewer data in `ingests`, not digester code, and it is not this
component's to edit.

## What to change here

1. Flatten Unicode punctuation in `searchable()`. It can only create matches,
   never destroy them: the mapping is many-to-one onto characters that already
   appear.
2. Score an ellipsis-joined quote by its segments, and record that it was
   elided rather than passing it silently. A splice of two true passages can
   still mislead, so the workbench should be able to see which quotes are
   spliced.
3. Recompute quote fidelity afterwards. Every published fidelity figure is a
   lower bound, ebooks and web records most of all.

Neither change touches extraction. Both are held until the model grid finishes,
because the code fingerprint covers the digester package and a mid-grid edit
would relabel every later cell.
