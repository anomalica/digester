#!/usr/bin/env bash
# The comparison, moved off the two longest records onto the densely-highlighted
# short ones. Master's point and it was the right variable: the comparison needs
# MEASURABLE records, not hard ones - Skywatcher carries 43 highlight units in
# 4,609 words and Fowler 69 in 10,001, so the human signal per word is as dense
# as Doty's at a fifth of the runtime.
#
# Most of the grid already existed on disk. These are the gaps.
set -uo pipefail
# Forward a stop to the children. Without this, `systemctl stop` kills only
# the wrapper: the extraction keeps running, systemd waits the full stop
# timeout, and Fedora's TimeoutStopFailureMode=abort drop-in then escalates to
# SIGABRT and writes a coredump. Observed as "State 'final-sigterm' timed out.
# Aborting" on a unit that had simply been asked to stop. These cells are
# stopped and restarted routinely as fixes land, so a clean exit is worth six
# lines.
_stop() {
	trap - TERM INT
	kill -TERM -$$ 2>/dev/null
}
trap _stop TERM INT
unset DIGESTER_USE_API ANOMALICA_USE_API OPENROUTER_API_KEY
export DIGESTER_ENTAILMENT=off
export ANOMALICA_CLI_TIMEOUT_S=2700 ANOMALICA_CLI_LONG_TIMEOUT_S=3000
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
BY=/home/mark/repos/anomalica/ingests/by-name
LOGS=/home/mark/repos/anomalica/digester/reports/model-sweep-logs
mkdir -p "$LOGS"
cd /home/mark/repos/anomalica/digester/workspace
SKY=$(ls "$BY"/2026-08-09-video-ross-coulthart-q-a-skywatcher*.md | head -1)
FOW=$(ls "$BY"/2026-08-08-video-raymond-fowler-on-ufos*.md | head -1)
run() {
	local model=$1 effort=$2 rec=$3 stem
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	local args=(--model "$model" --digests-root /home/mark/repos/anomalica/digests --variant-only)
	[ "$effort" != "low" ] && args+=(--run-label "effort-$effort")
	for attempt in 1 2 3 4 5 6; do
		echo "[$model/$effort] ${stem:0:40} attempt $attempt $(date +%H:%M)"
		t0=$(date +%s)
		ANOMALICA_CLI_EFFORT=$effort timeout 5400 python3 -m digester.cli extract "$rec" "${args[@]}" \
			>"$LOGS/$stem.$model.$effort.log" 2>&1
		rc=$?
		el=$(($(date +%s) - t0))
		case $rc in
		0)
			echo "    ok in ${el}s"
			return 0
			;;
		77)
			echo "    paced after ${el}s; waiting 15 min"
			sleep 900
			;;
		*)
			echo "    FAILED exit $rc after ${el}s"
			tail -2 "$LOGS/$stem.$model.$effort.log" | sed 's/^/      /'
			return $rc
			;;
		esac
	done
	return 77
}
run haiku low "$FOW"   # completes the model trio on the second record
run opus medium "$SKY" # effort x model, cheapest record first
run haiku medium "$SKY"
run opus medium "$FOW"
run haiku medium "$FOW"
echo "=== short sweep done $(date +%H:%M) ==="
