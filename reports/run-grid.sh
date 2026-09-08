#!/usr/bin/env bash
# The model grid, re-run at ONE pinned configuration.
#
# The September grid could not answer the two questions it was built for,
# because half of it sat at one claims prompt and half at another and the grade
# table showed neither. This run fixes the configuration for every cell: the
# prompts are read from a SNAPSHOT beside this script rather than from the live
# prompt directory, so an edit landing mid-grid cannot reach a cell. The
# variant filename hashes the prompt text, so those snapshots produce the same
# fingerprint a non-override run of identical text would.
#
# Two questions, in this order, because a partial grid must answer one thing
# whole rather than two things halfway:
#   1. Does Opus actually beat Sonnet?  Fowler, three arms each, interleaved.
#   2. Does Haiku hold on a short record? Skywatcher, three arms each.
# Then the two cells that complete the grid.
#
# The arms are interleaved so that stopping early leaves the arms balanced.
# THE CALL CACHE IS OFF: an identical call replays its stored response, so
# cached repeats would return byte-identical digests and measure nothing.
set -uo pipefail

# RUN FROM A COPY, NEVER FROM THE REPOSITORY FILE.
# Bash does not read a script into memory - it reads incrementally and keeps a
# byte offset - so editing this file while it runs shifts every offset after the
# cursor and the shell resumes mid-token. It presents as a bug on a line that is
# correct: an edit at 22:00 killed an hour-old sweep with "line 39: rec: unbound
# variable" on a call that passes all three arguments. Exec'ing a snapshot makes
# the repository copy editable at any time without touching the run.
if [ "${GRID_FROM_COPY:-}" != "1" ]; then
	_copy=$(mktemp /tmp/run-grid.XXXXXX.sh)
	cat "$0" >"$_copy"
	chmod +x "$_copy"
	GRID_FROM_COPY=1 exec "$_copy" "$@"
fi
_stop() {
	trap - TERM INT
	kill -TERM -$$ 2>/dev/null
}
trap _stop TERM INT

unset DIGESTER_USE_API ANOMALICA_USE_API OPENROUTER_API_KEY
export DIGESTER_CALL_CACHE=off
export DIGESTER_ENTAILMENT=off
export ANOMALICA_CLI_TIMEOUT_S=2700 ANOMALICA_CLI_LONG_TIMEOUT_S=3000
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
CONFIG=/home/mark/repos/anomalica/digester/reports/grid-config
export DIGESTER_NODES_PROMPT_FILE="$CONFIG/nodes.txt"
export DIGESTER_CLAIMS_PROMPT_FILE="$CONFIG/claims.txt"

BY=/home/mark/repos/anomalica/ingests/by-name
LOGS=/home/mark/repos/anomalica/digester/reports/grid-logs
mkdir -p "$LOGS"
cd /home/mark/repos/anomalica/digester/workspace
FOW=$(ls "$BY"/2026-08-08-video-raymond-fowler-on-ufos*.md | head -1)
SKY=$(ls "$BY"/2026-08-09-video-ross-coulthart-q-a-skywatcher*.md | head -1)

# Mark cleared the weekly allowance to 83%. Read it before every cell rather
# than trusting a running total: other sessions draw on the same plan.
#
# TWO WINDOWS, AND THE SHORT ONE BITES FIRST. The weekly window is the budget;
# the five-hourly session window is the throttle, and it moves faster. Checking
# only the weekly one means an exhausted session fails a cell, the loop moves
# on, and every remaining cell fails in minutes - the grid would report itself
# finished having measured nothing. So a full session WAITS, it does not fail:
# the window resets on its own and the run has all night.
CEILING=83
SESSION_PAUSE=86
window_used() {
	(cd /home/mark/repos/anomalica/scheduler && timeout 60 python3 -c "
import sys
from backend import usage
name = sys.argv[1]
w = [x for x in usage._claude_usage({}).get('windows', []) if x['name'] == name]
print(int(w[0]['used']) if w else 100)
" "$1" 2>/dev/null) || echo 100
}
weekly_used() { window_used weekly; }
session_used() { window_used session; }

await_session() {
	local used waited=0
	used=$(session_used)
	while [ "$used" -ge "$SESSION_PAUSE" ] && [ "$waited" -lt 21600 ]; do
		echo "    session at ${used}%, waiting 10 min for the window to reset"
		sleep 600
		waited=$((waited + 600))
		used=$(session_used)
	done
}

# The variant filename a cell will write, so a restart skips finished work
# rather than paying for it twice. A sweep restarted at 22:08 re-ran a cell that
# had completed at 21:58 because nothing checked.
variant_of() {
	python3 -c "
import sys
from digester import extract
from digester.digest_store import prompt_sha8
print(f'{sys.argv[1]}.{prompt_sha8(extract.prompt_provenance())}.{sys.argv[2]}.yaml')
" "$1" "$2" 2>/dev/null
}

cell() {
	local model=$1 rec=$2 label=$3 stem used want
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	want=$(variant_of "$model" "$label")
	if [ -n "$want" ] && [ -f "/home/mark/repos/anomalica/digests/variants/$stem/$want" ]; then
		echo "[$model $label] ${stem:0:38} already on disk, skipping"
		return 0
	fi
	await_session
	used=$(weekly_used)
	if [ "$used" -ge "$CEILING" ]; then
		echo "=== STOPPING at ${used}% weekly, ceiling ${CEILING}% - ${model}/${label} not started"
		exit 0
	fi
	echo "[$model $label] ${stem:0:38} weekly ${used}% session $(session_used)% $(date +%H:%M)"
	local t0 rc el
	t0=$(date +%s)
	timeout 5400 python3 -m digester.cli extract "$rec" \
		--model "$model" \
		--digests-root /home/mark/repos/anomalica/digests \
		--variant-only --run-label "$label" \
		>"$LOGS/$stem.$model.$label.log" 2>&1
	rc=$?
	el=$(($(date +%s) - t0))
	if [ $rc -eq 0 ]; then
		echo "    ok in ${el}s"
	else
		echo "    FAILED exit $rc after ${el}s"
		tail -3 "$LOGS/$stem.$model.$label.log" | sed 's/^/      /'
	fi
}

echo "=== grid start $(date +%H:%M), weekly $(weekly_used)%, ceiling ${CEILING}%"
for arm in 1 2 3; do
	cell sonnet "$FOW" "grid-$arm"
	cell opus "$FOW" "grid-$arm"
done
for arm in 1 2 3; do
	cell sonnet "$SKY" "grid-$arm"
	cell haiku "$SKY" "grid-$arm"
done
cell haiku "$FOW" "grid-1"
cell opus "$SKY" "grid-1"
echo "=== grid done $(date +%H:%M), weekly $(weekly_used)%"
