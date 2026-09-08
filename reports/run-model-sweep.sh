#!/usr/bin/env bash
# Model and effort comparison on the two densely-highlighted records, with the
# FIXED coverage metric (digester c9ca17e). Replaces a baseline that was invalid
# twice over: every extraction ever ran at hardcoded effort low, and recall
# over-counted overlapping claims until that commit.
#
# ORDERED BY INFORMATION, NOT BY SYMMETRY. If the quota runs out mid-batch the
# cells already done must answer something: the model axis on one record first
# (and sonnet/Doty doubles as the fair binding baseline the account pass needs),
# then effort on that record, then the whole trio again on the second record as
# replication. A half-finished symmetric grid answers nothing.
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
export DIGESTER_ENTAILMENT=off # the GPU is shared; the check is not the measurement
export ANOMALICA_CLI_TIMEOUT_S=2700 ANOMALICA_CLI_LONG_TIMEOUT_S=3000
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
BY=/home/mark/repos/anomalica/ingests/by-name
LOGS=/home/mark/repos/anomalica/digester/reports/model-sweep-logs
mkdir -p "$LOGS"
cd /home/mark/repos/anomalica/digester/workspace
DOTY=$(ls "$BY"/-video-ex-afosi-agent-shares-wild-alien-encounters*.md | head -1)
STEWART=$(ls "$BY"/2026-01-02-video-the-alien-interview-tape-might-be-real*.md | head -1)

run() { # model effort record label
	local model=$1 effort=$2 rec=$3 stem
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	local args=(--model "$model" --digests-root /home/mark/repos/anomalica/digests --variant-only)
	[ "$effort" != "low" ] && args+=(--run-label "effort-$effort")
	for attempt in 1 2 3 4 5 6 7 8; do
		echo "[$model/$effort] ${stem:0:44} attempt $attempt $(date +%H:%M)"
		t0=$(date +%s)
		ANOMALICA_CLI_EFFORT=$effort timeout 9000 python3 -m digester.cli extract "$rec" "${args[@]}" \
			>"$LOGS/$stem.$model.$effort.log" 2>&1
		rc=$?
		el=$(($(date +%s) - t0))
		case $rc in
		0)
			echo "    ok in ${el}s"
			return 0
			;;
		77)
			echo "    paced after ${el}s; waiting 20 min"
			sleep 1200
			;;
		*)
			echo "    FAILED exit $rc after ${el}s"
			tail -2 "$LOGS/$stem.$model.$effort.log" | sed 's/^/      /'
			return $rc
			;;
		esac
	done
	echo "    gave up"
	return 77
}

# 1. model axis on Doty (sonnet first: it is also the account pass's baseline)
run sonnet low "$DOTY"
run opus low "$DOTY"
run haiku low "$DOTY"
# 2. effort axis on the same record
run sonnet medium "$DOTY"
run opus medium "$DOTY"
# 3. replication on the second record
run sonnet low "$STEWART"
run opus low "$STEWART"
run haiku low "$STEWART"
run sonnet medium "$STEWART"
echo "=== model sweep done $(date +%H:%M) ==="
