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
CEILING=83
weekly_used() {
	(cd /home/mark/repos/anomalica/scheduler && timeout 60 python3 -c "
from backend import usage
w = [x for x in usage._claude_usage({}).get('windows', []) if x['name'] == 'weekly']
print(int(w[0]['used']) if w else 100)
" 2>/dev/null) || echo 100
}

cell() {
	local model=$1 rec=$2 label=$3 stem used
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	used=$(weekly_used)
	if [ "$used" -ge "$CEILING" ]; then
		echo "=== STOPPING at ${used}% weekly, ceiling ${CEILING}% - ${model}/${label} not started"
		exit 0
	fi
	echo "[$model $label] ${stem:0:38} weekly ${used}% $(date +%H:%M)"
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
