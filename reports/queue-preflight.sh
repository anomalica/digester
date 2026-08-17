#!/usr/bin/env bash
# Refuse to start the digestion queue while it would bake in work we know is
# about to be thrown away.
#
# THE HAZARD IS A STEP FUNCTION ATTACHED TO A SWITCH. External passages - clips a
# record quotes from elsewhere - are being removed from the pre-digest, because a
# container's transcription of a clip is worse than the original we already hold
# and extracting both manufactures false corroboration. Every marked record
# digested BEFORE that lands bakes the clip text into its digest and joins the
# re-digest list. At the time of writing that is 1 record; 7 more sit in the
# queue, so the ordinary "resume everything" action multiplies the bill eightfold
# and gives no sign it has done so.
#
# So the order is: strip lands, PREP_VERSION bumps, THEN the queue is re-enabled.
# This enforces it rather than documenting it.
#
# The check is BEHAVIOURAL, not a version number: it feeds a marked passage
# through the real `materialise` and looks at what comes out. That cannot go
# stale, needs no constant kept in step, and retires itself - the day stripping
# lands the probe passes and this never fires again.
set -u
ANOMALICA=/home/mark/repos/anomalica
LIST=${LIST:-$ANOMALICA/digester/reports/books.list}

strips_external=$(
	PYTHONPATH=$ANOMALICA/anomalica-common/src python3 - <<-'PY' 2>/dev/null
		from anomalica_common.pre_digest import materialise
		probe = 'a {{external-start: [x1, "src"]}}QUOTEDTEXT{{external-end: x1}} b'
		print("no" if "QUOTEDTEXT" in materialise(probe) else "yes")
	PY
)

# Fail closed: an unreadable probe is not permission to proceed.
if [ "$strips_external" = "yes" ]; then
	exit 0
fi
if [ -z "$strips_external" ]; then
	echo "queue-preflight: cannot determine whether external passages are stripped - refusing" >&2
	exit 1
fi

marked=0
while read -r slug; do
	[ -n "$slug" ] || continue
	link="$ANOMALICA/ingests/by-name/${slug%.md}.md"
	[ -e "$link" ] || link="$ANOMALICA/ingests/by-name/${slug%.md}.v2.md"
	[ -e "$link" ] || continue
	if grep -q '{{external-start' "$link" 2>/dev/null; then
		marked=$((marked + 1))
	fi
done <"$LIST"

compilations=0
while read -r slug; do
	[ -n "$slug" ] || continue
	link="$ANOMALICA/ingests/by-name/${slug%.md}.md"
	[ -e "$link" ] || link="$ANOMALICA/ingests/by-name/${slug%.md}.v2.md"
	[ -e "$link" ] || continue
	# A compilation whose papers are not yet delimited. Digesting it attributes
	# every claim to the CONTAINER - Robert L. Forward's work sourced to "NASA
	# workshop proceedings", his person node uncredited, and the container's
	# editors credited for work they did not do. False attestation, the same
	# class the external-passage marker exists to prevent.
	#
	# WHAT A PASSING CHECK MEANS: "this known record is not queued unannotated".
	# It does NOT mean "no compilations are queued", and it does NOT mean the
	# boundaries are right. Two blind spots, in different dimensions:
	#
	#   1. Known instance, not a detector. It keys on the scanned "NEXT DOCUMENT"
	#      divider pages - one publisher's physical convention, transcribed source
	#      content rather than anything we emit, and the only instance in 225 pdf
	#      and ebook records. It says nothing about the next compilation.
	#   2. Presence, not correctness. It tests for the ABSENCE of any
	#      `<!-- document:` annotation, so 3 annotations where 17 papers exist
	#      stands it down and 14 papers digest attributed to the container. Same
	#      if all 17 land carrying the container's creators instead of each
	#      paper's author.
	#
	# Blind spot 2 is deliberately NOT closed here by counting dividers against
	# annotations, because no divider count maps to the paper count: this record
	# has 44 "NEXT DOCUMENT" occurrences - 23 image annotations, 6 headings, 15
	# other - against 17 papers. Any threshold would be a guess presented as a
	# check, which is worse than a stated limit.
	#
	# Correctness belongs where the annotations are produced: the ingester
	# verifies they are present, that there are 17, and that each paper's
	# `creators` is its own author before anything downstream sees it. This is
	# only the backstop against the annotation never landing at all.
	#
	# Both blind spots are false negatives, so neither leaves us worse off than
	# having no check - but a passing check must not be read as verification.
	if grep -q "NEXT DOCUMENT" "$link" 2>/dev/null && ! grep -q "<!-- document:" "$link" 2>/dev/null; then
		compilations=$((compilations + 1))
		echo "queue-preflight: $slug is a compilation with no document boundaries" >&2
	fi
done <"$LIST"

if [ "$compilations" -gt 0 ]; then
	cat >&2 <<-EOF
		queue-preflight: REFUSING TO START.

		$compilations queued record(s) hold several documents with no boundaries marked.
		Digesting one attributes every claim to the container rather than to the paper's
		real authors, which is wrong in the data and expensive to unpick.

		Re-ingest it so the papers are delimited, then start the queue. This check
		passes by itself once that is done.
	EOF
	exit 1
fi

if [ "$marked" -gt 0 ]; then
	cat >&2 <<-EOF
		queue-preflight: REFUSING TO START.

		External passages are not yet stripped from the pre-digest, and $marked queued
		record(s) carry external markers. Digesting them now bakes the quoted clip text
		into their digests and adds every one to the re-digestion list.

		Land the strip and bump PREP_VERSION first, then start the queue. This check
		passes by itself once that is done - nothing here needs editing.
	EOF
	exit 1
fi
exit 0
