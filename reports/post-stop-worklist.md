# Digester work queued behind Mark's processing stop

Accumulated 2026-08-13 to 2026-08-17 from bus design threads while all processing
was halted. Nothing here has been run, measured, or implemented. Written down
because it existed only in bus messages, which do not survive a session.

Costs no allowance unless marked otherwise.

## Order matters for the first three

The external-footage strip and the compilation re-ingest both get more expensive
if the record queue resumes first. Two `ExecStartPre` interlocks on
`anomalica-record-queue.service` now enforce this (master, b33bc58 and dbe8595),
so the queue refuses to start rather than relying on anyone remembering.

1. **External-passage strip.** `external-start` / `external-end` join the strip
   list, so quoted footage is removed from the pre-digest exactly as `irrelevant`
   regions are. Mark's decision, reversing my earlier ruling that the text should
   be kept. Bill is 1 record today, 8 if the queue digests the marked ones first
   (7 of the 9 marked records are in `books.list`).
2. **`PREP_VERSION` bump** follows the strip, since materialise output changes.
3. **Then** re-enable the queue.

## Bugs and gaps found but not fixed

- **`compare_discovery.py:142`** instructs the extraction model to emit person
  names as "Last, First Middle". `node-types.md:137` superseded that on
  2026-06-29 in favour of natural order, and I bumped the registered prompts
  (3903b64) without knowing this copy existed. FIRST determine whether the script
  is in any live path: if it is, there are two extraction routes with
  incompatible name conventions, which is worse than the string, and an alias map
  keyed on spelling variants will not catch a whole naming scheme. If it is a
  dead benchmark, delete rather than edit. It also cites decisions 0023 and 0026,
  neither of which exists - the directory jumps 0021 to 0027.
- **No prompt should live outside the prompt registry.** Registered prompts are
  versioned and their shas travel into the digest; an inline copy is invisible to
  all of that and drifts silently, which is exactly what happened above.
- **Audit registered prompt CONTENT for leaked vocabulary.** Separate problem
  from the registry, and I nearly filed them together. The ingester found their
  worked example was a complete record carrying `schema: anomalica/record/1` and
  `source_type: pdf`, so the model learned our frontmatter vocabulary and echoed
  it back (their fix: ae2c25f). Registration fixes a prompt nothing can SEE; it
  is silent on a prompt teaching the model something it should not KNOW. Check
  `nodes.txt`, `claims.txt`, `cast.txt` for record-format terms.
- **Chunk boundaries must not bisect a display equation.** Bodies now carry LaTeX
  (`\[ ... \]` display, `\( ... \)` inline) per ingest-format.md "Mathematics"
  (e157803). A split inside a display equation gives one chunk an unterminated
  fragment and the next an orphaned tail. Not yet triggered - no record carries
  an equation.
- **Quote fidelity will degrade on LaTeX**, and the cause will not be the LaTeX.
  Models tidy it: dropping `\,`, rewriting `F_\text{sky}` as `F_{sky}`. The
  aligner absorbs whitespace differences but not that, so the fidelity check
  classifies a tidied equation as a fabricated fragment. Two fixes: a prompt line
  requiring byte-exact reproduction inside math delimiters, and treating a
  normalised-LaTeX near-match as elided rather than broken.

## Metadata refresh - new, and it saves real allowance

Ingests normalised frontmatter across 60 records (ledger:
`/home/mark/repos/anomalica/ingests/.ai/metadata-refresh-2026-08-16.json`,
schema `anomalica/metadata-refresh/1`, keyed by record hash).

**Those 60 must NOT be re-digested.** Every field touched is COPIED into the
digest's record block, never fed to the model - `date_published` lands as `date`
after read-time normalisation, `speakers` is carried verbatim, `creators`
resolves `producer`. Re-extraction would spend model calls recomputing claims
that cannot change. At ~2.8 allowance points per book that is one of the more
expensive no-ops available.

Needed instead: a refresh path that rewrites the record block from current
frontmatter, leaving nodes, claims and `pre_digest` untouched. Deterministic, no
model calls.

**It must REFUSE a record whose `speakers` roster changed while its body
annotations did not.** `speakers` is the one frontmatter field whose content also
appears in the body (as `<!-- speaker: -->` annotations, which the model does
see), so a roster-only refresh yields a digest whose roster reads "Jesse Michels"
while its claims reference a node "Jessie Michaels" - internally inconsistent and
looking correct from either half. Ingests' pass avoided this (all 22
roster-changed records were also body-edited, so the refresh list is 38 pure
copied-field updates), but by their judgement, not by enforcement.

Their 29 body-edited records DO need re-extraction and self-detect via
`digester stale-records`. SPENDS ALLOWANCE.

## Health checks

- **`over_marked` must count external and irrelevant removals separately.** Once
  external passages strip, an archival-footage podcast that is 60% clips lands at
  0.40 survival against a 0.5 floor - flagged while marked entirely correctly.
  60% external is fine; 60% irrelevant is suspicious.
- DONE 2026-09-02 (health.pre_digest_freshness). **Freshness must compare recomputed hashes BEFORE reporting a version
  difference.** It currently reports on `prep_version != current` first, so the
  `PREP_VERSION` bump above will flag the entire corpus stale and bury the one
  genuine re-digest. No record carries external markers yet, so the bump changes
  no existing output.
- **Roster-versus-attribution check.** A claim attributed to a name absent from
  the source's own `speakers` roster, and a roster member yielding no claims,
  are both computable and neither is computed. This is the reason `speakers` was
  carried into the digest in the first place. Ingests' number is why it matters:
  68% of speaker turns corpus-wide are bare "Speaker N" against 1% inside
  reviewed records, so the person layer's problem is overwhelmingly MISSING
  attribution, not misspelt attribution.
- **Assert the membership of the copied-and-also-in-body set**, today
  `{speakers}` - not the emptiness of the dangerous class. Emptiness is
  structural and always true, so it says nothing when a field joins the other
  set. Membership fails the moment someone adds one. A test that can only pass is
  what was removed from `health.py` last week.

## Contained-document attribution

`ecc3c13e9ee7` (NASA Breakthrough Propulsion Physics Workshop Proceedings, 17
papers, 44 "NEXT DOCUMENT" stamps) is the ONLY compilation in the corpus - the
ingester swept all 225 pdf and ebook records. It is ingested and NOT digested, so
there is no false attribution in the graph today. It is not in `books.list`
either, but `books.list` is regenerable and "every undigested record" picks it
up.

Digested as-is, every claim would be sourced to the container: Forward's claims
attributed to "NASA workshop proceedings", his person node uncredited, the
container's creators credited for work they did not do.

The annotation is already ratified (`ingest-format.md` e157803, "### Document
boundary", attribution rule tied to ADR 0044) and the ingester's prompt emits it
(dc9ded4). **The digester side is the only gap** - `document` is not in the
annotation key set, so the annotation would pass through unrecognised.

Design: a digest-level `documents:` table plus a `doc3:235-331` location frame,
reusing the chapter-relative machinery in `realign.py`. Honest as a FRAME because
a contained document is physically present in the record - unlike quoted external
footage, which is present as text but was uttered elsewhere and therefore needs a
separate attestation field. Two problems that look identical and want different
answers.

Build the parser side so something is waiting to consume the annotation, rather
than the annotation landing and sitting unread - which is how the `release` block
went nowhere for a fortnight.

## Waiting on Mark, not on me

- Lifting the processing stop.
- Clearing the ingester's 416-page re-extraction of the proceedings (SPENDS
  ALLOWANCE). They will verify 17 boundaries and each paper's own `creators`
  before anything downstream sees it, and report a short count rather than
  re-running until it looks right.

## Shared transport: metered authorisation is one process-wide flag

Added 2026-08-18 after the ingester's vision provider authorised spend in its
constructor (found, reported, fixed same day - their fix verified: constructor
can no longer authorise, `spend_confirmed` precedes construction, 416 pages
estimates $0.97 and refuses without an explicit yes).

**The underlying design property survives their fix.**
`_metered_spend_authorised` is a single module-level global in
`anomalica_common/llm/transport.py`, and all four paid call sites read it. So
ANY component that authorises does so for every metered path in the process, not
just its own. Demonstrated: before the call all three kinds block; after it, the
Anthropic API path passes too, having nothing to do with the component that
authorised.

That is what turned a constructor in the ingester's vision provider into
authorisation for the digester's Anthropic calls. Their fix removes today's
instance; the next component to make the same mistake exposes this path again.

Proposal, not built: scope authorisation per kind -
`authorise_metered_spend("OpenRouter vision")` and `_require_metered(kind)`
checking that kind. Small change, bounds the blast radius to the path that was
actually approved, and keeps the single-source property that made the gate
correct in the first place. Needs agreement from every consumer of the shared
transport, so it is a discussion rather than a commit.

Also worth knowing, and NOT a digester item: the ingester's per-run auto-approve
ceiling (`INGEST_SPEND_CEILING_USD`, default $0.50) guards one expensive run and
is blind to many cheap ones - 200 documents at $0.30 is $60 with no explicit yes
at any point. The OpenRouter account balance is the only aggregate control. The
ingester has routed batch-level approval to the scheduler, which is the only
layer that can see a batch before it fans out.

## Prompt and parser fixes from the 2026-08-21/22 spec work

Four separate cases in one week where a construct crossed a component boundary
and the receiving side had been written for a form that had since changed.
Recorded together because the fix is the same shape each time: test the CLASS of
thing, never enumerate the notations.

**1. nodes prompt -> v5. The anonymous-actor guard is live and it FAILS.**
`Senior U.S. Intelligence Officer (Anonymous)` (30 refs) was minted 2026-08-01 on
nodes prompt v4, with the guard in force. The rule states a category ("redacted /
anonymous actors") but teaches it with a single worked example, `"USS Louisville
Officer (redacted)"` - and a near-identical shape still got through, so the model
is matching the EXAMPLE rather than applying the category. A worked example can
narrow a rule as effectively as a regex can.

Shape cannot be the test, because these are structurally identical and land on
opposite verdicts (workbench, ingest-format.md f53fac1):

    Sally (Budd Hopkins abductee)                  NAME - node, matches across records
    Senior U.S. Intelligence Officer (Anonymous)   NOT  - description, no node

The replacement rule, now in the spec: **does the value contain an actual name -
a given name, a surname, or a pseudonym? If what remains after roles, ranks,
organisations and qualifiers is nothing, it names nobody, however specific.**
`Sally` is a name; `Dr. X` and `A Friend` are pseudonyms and therefore names;
`Senior U.S. Intelligence Officer` is a job.

Also fix in the same pass: line 9 currently says to attribute an anonymous actor
to "the relevant organisation instead". That is now scoped by the spec to an
actor a document DESCRIBES, and must NOT apply to somebody SPEAKING - attributing
a person's testimony to a body asserts the body said what the person said, and an
`[audience member]` often has no organisation to reassign to at all.

This one rule subsumes the speaker case, so it is one rule and not two. Structural
position stays the disambiguator: a speaker description is only ever the WHOLE
value of a `<!-- speaker: ... -->` comment, never inline - so `[sic]`, `[laughter]`
and an editor's `[clarification]` inside a quote are untouched source content.
Accept bracketed and unbracketed `speaker <n>` alike as diarisation ids.

**2. materialise does NOT strip `{{classification: ...}}`** - our own annotation
syntax is reaching the model as prose. The overlay stripper is an allow-list of
four families and requires a `-suffix`:

    _OVERLAY_MARKER = re.compile(r"\{\{(?:highlight|link|note|cites)-[a-z]+:[^}]*\}\}")

Verified: `{{classification: SECRET//NOFORN}}` survives materialise untouched.
8 occurrences across 3 records, 2 already digested (misrep-7816710,
undated-pdf-uap-sighting-report). Do NOT fix by adding `classification` to the
alternation - invert it to strip the whole `{{...}}` family by default with the
note-text exemption explicit, so a NEW annotation type is stripped unless someone
deliberately exempts it. Failure mode becomes "an annotation went missing"
(visible) rather than "our syntax reached the model" (invisible).

Note the ingester strips these and we do not, so the same construct is handled
oppositely across the boundary. The spec sentence "the tagged form does not
survive stripping" is component-specific while reading as universal.

**3. `_producer_from_creators` skips `{{redacted}}` but not `[redacted]`.**
The bracket notation now covers creators, and the guard is brace-only:

    ['[redacted]', 'Jane Doe']  ->  producer '[redacted]'   <- steps past the real name

The function's contract is "the first creator that is a REAL name" and it does
the opposite. `producer` is carried into the digest record block, so it travels.
Nothing exposed today - no record carries a bracketed creator yet. Fix with a
notation-agnostic test (a creator that is ENTIRELY a bracketed or braced token is
not a name), not by adding brackets to the regex.

**4. My own measurement had the same defect.** I reported n=2 descriptive person
nodes by querying `unnamed%`, `[%`, `speaker %` - notation prefixes, so a
capitalised description was structurally invisible. The real answer was 5.

**And the lesson I first drew from that was ALSO wrong.** I concluded "sweep by
name shape, not by prefix" and wrote it down. The assimilator measured it: a shape
test flags `Colonel Friend` (a real surname) and STILL misses `Senior U.S.
Intelligence Officer`, because "U.S." tokenises into capitalised tokens that read
as a proper noun. Shape is worse than the prefix it replaced. Workbench corrected
their own spec line (72a069d) rather than let me inherit it.

So NO corpus sweep is trustworthy here, and there is no positive marker to search
for either - a correctly-applied rule produces an ABSENCE, not a flag. **v5 cannot
be verified by scanning.** The only real test is a regression one: re-extract the
specific records that produced these five and assert the node does not appear.
That needs model calls, so it is a COST, not a free check.

Fifth node, and the case the name test alone does not settle:
`"the boy" (hybrid child at Strieber's cabin)`, 27 refs. Workbench's tie-breaker,
now in the spec and going into v5 alongside the name test: **would another record
use this handle to mean this same person?** `A Friend` yes - a pseudonym that
travels. `"the boy"` no - it means whoever that record was discussing. A common
noun in quotation marks is a description wearing a costume.

**5. Hand-edited digests are indistinguishable from model output.** The assimilator
is rewriting three person names directly in digests (right call - the graph is
derived, so a graph-only retirement is undone by the next import). But a digest
carries `model`, `prompts`, `extracted_at`, `ai_usage`, `run_kind` and
`pre_digest.sha256`, and NOTHING that can say a human changed a value afterwards.
After the rewrite the file asserts sonnet on nodes-v4 emitted a value it did not.

`stale-records` cannot catch it and correctly stays silent: the body is untouched,
so `pre_digest.sha256` still matches. The one check that would notice a digest
diverging from its extraction is the one that cannot fire for this.

Consequence for benchmarking: the 27-model sweep rests entirely on a digest being
what a model emitted. Unmarked hand-edits mean future comparisons silently mix
edited and unedited artefacts with no way to separate them. Asked workbench for a
top-level `curation:` block naming what changed, by whom and why.

## HIGHEST PRIORITY: the digest round trip destroys load-bearing fields

Found 2026-08-22 while auditing `yaml_format.py` after the curation-block gap.
Worse than that gap and worth doing first.

`parse_digest_yaml` -> `parsed_dict_to_digest_yaml` silently loses:

    curation        in=True   out=False
    pre_digest      in=True   out=False    <- the reproducibility hash
    run_kind        in=True   out=False    <- production vs comparison
    record.speakers in=True   out=False
    record.<any new field>    out=False

**TWO faults, not one.** The PARSER captures `pre_digest`, `run_kind` and the
whole `record` block (speakers included) into `frontmatter` correctly; it drops
only `curation`. The EMITTER then fails to restore several things the parser
kept. Fixing the parser alone leaves the worse half in place.

Why each loss matters:

- `pre_digest.sha256` gone = the digest can never again be checked against its
  source. `pre_digest_freshness` reads it and would report orphaned or a version
  mismatch instead of a real answer.
- `run_kind` gone = a comparison run becomes indistinguishable from production,
  the exact confusion the field was added (aa9556f) to end.
- `speakers` gone = the source's own roster disappears, which is the ONLY handle
  for "a claim attributed to someone absent from the roster" - the check queued
  for the 68%-bare-`Speaker N` problem. The round-trip fault would silently
  disable a check aimed at the same class of defect.

Near-miss: the assimilator was about to hand-edit three digests. Had that been a
load-modify-write through this module, those files would have lost their hash,
run_kind and roster IN THE SAME COMMIT that added a `curation` block certifying
the file was carefully edited. Caught before the write; workbench stopped them to
confirm the edit method.

**Fix: pass-through on both sides, plus a ROUND-TRIP TEST that fails when a field
is added to one side and not the other.** Of the five enumeration failures this
week this is the only one whose test cannot rot - the others rely on someone
remembering a rule, this one fails automatically on the next asymmetric change.

Note for whoever reads the parser docstring ("everything is offered and the
consumer chooses"): it is true of the parser and says nothing about the emitter.
A round trip is a property of a PAIR of functions and no single docstring can
state it. Neither half was lying, which is why reading them separately missed it.

## grade-record crashes on a record with no gold units (2026-09-02)

`rows.sort(key=lambda x: -x[1]["recall"])` and the row printer both assume recall is a
number; a record with 0 reviewer highlight units yields None and the command
tracebacks. Seen on misrep-7816710, ic-watchdog, bill-hamilton. Sort None last and
print n/a.

## Effort test (2026-09-02): recall figures were computed on a broken grader

The stage-1a comparison of Sonnet at low against medium was scored before
`_overlap` was fixed (digester c9ca17e). Until then coverage summed each claim's
overlap with a reviewer highlight instead of taking their union, so a character
covered by three claims counted three times and recall rose with repetition -
the bias favours whichever variant emitted more claims, which is exactly the
axis the test was measuring.

Recomputed on the fixed grader, the direction is unchanged and the conclusion
stands - medium did not beat low on any of the four records:

| record     | gold | low (old) | low (new) | medium (old) | medium (new) | delta |
|------------|-----:|----------:|----------:|-------------:|-------------:|------:|
| skywatcher |   43 |     0.926 |     0.913 |        0.906 |        0.892 | -0.022 |
| grusch     |   26 |     0.869 |     0.852 |        0.862 |        0.858 | +0.007 |
| fowler     |   69 |     0.834 |     0.834 |        0.797 |        0.797 | -0.037 |
| nolan      |   48 |     0.594 |     0.585 |        0.559 |        0.559 | -0.026 |

The old figures were inflated by 0 to 1.7 points and the inflation was larger
for the low-effort variant on three of four records, so the corrected gap
between low and medium is slightly WIDER, not narrower. Any other conclusion
drawn from a recall number dated before 2026-09-04 should be recomputed rather
than trusted; the model comparison that produced the digest priority list was
run after the fix.
