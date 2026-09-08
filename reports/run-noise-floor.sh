#!/usr/bin/env bash
# Re-establish the run-to-run noise floor (cleared by Mark via master, 2026-09-04).
# Three IDENTICAL runs of one model on the Fowler record, which carries reviewer
# highlights, each under its own --run-label so every arm survives - the previous
# attempt lost an arm because an unlabelled re-run overwrites its own variant.
#
# THE CALL CACHE MUST BE OFF. An identical (model, prompt, text, schema) call
# replays the stored response, so cached repeats would return byte-identical
# digests and report a noise floor of exactly zero - a wrong answer that looks
# like a clean one.
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
export DIGESTER_CALL_CACHE=off
export DIGESTER_ENTAILMENT=off
export ANOMALICA_CLI_TIMEOUT_S=3600 ANOMALICA_CLI_LONG_TIMEOUT_S=7200
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
MODEL="${1:?usage: run-noise-floor.sh <model> [record-glob]}"
# Default: the Fowler interview, 69 highlight units. Overridable because a model
# that cannot complete a record measures its reliability, not its variance -
# kimi-k3 failed Fowler on malformed JSON after 44 minutes, so its arms run on a
# record it completes.
GLOB="${2:-2026-08-08-video-raymond-fowler-on-ufos*}"
LOGS=/home/mark/repos/anomalica/digester/reports/noise-floor-logs
mkdir -p "$LOGS"
cd /home/mark/repos/anomalica/digester/workspace
rec=$(ls /home/mark/repos/anomalica/ingests/by-name/$GLOB.md 2>/dev/null | head -1)
[ -z "$rec" ] && {
	echo "no record matching $GLOB"
	exit 1
}
echo "record: $(basename "$rec")"
safe=$(echo "$MODEL" | tr '/' '-')
stem=$(basename "$rec")
stem=${stem%.v2.md}
stem=${stem%.md}
for arm in 1 2 3; do
	for attempt in 1 2 3 4 5 6; do
		echo "[$MODEL] repeat-$arm (attempt $attempt) $(date +%H:%M)"
		t0=$(date +%s)
		timeout 10800 python3 -m digester.cli extract "$rec" --model "$MODEL" \
			--digests-root /home/mark/repos/anomalica/digests --variant-only \
			--run-label "repeat-$arm" >"$LOGS/$stem.$safe.repeat-$arm.log" 2>&1
		rc=$?
		el=$(($(date +%s) - t0))
		case $rc in
		0)
			echo "    ok in ${el}s"
			break
			;;
		77)
			echo "    paced after ${el}s; waiting 10 min"
			sleep 600
			;;
		*)
			echo "    FAILED exit $rc after ${el}s"
			tail -3 "$LOGS/$stem.$safe.repeat-$arm.log" | sed 's/^/      /'
			break
			;;
		esac
	done
done
echo "=== $MODEL noise-floor arms done $(date +%H:%M) ==="
