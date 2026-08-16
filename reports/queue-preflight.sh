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
