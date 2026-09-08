#!/usr/bin/env bash
# Does kimi-k3 fail on LONG records, or on records with a LARGE NODE DIRECTORY?
# Every kimi success has <=22 nodes; every failure has >=101. Length and node
# count are confounded in that set, because long records have more nodes. This
# separates them: a 12 KB record - a third the size of records kimi completes
# comfortably - whose node directory is 455 entries, the largest in the corpus.
# If it fails, the limit is the locked-node enum the claims pass serialises into
# the prompt, not the document; and the operator's "under 40 KB" rule does not
# protect against it, because this record is 12 KB.
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
# THE GUARD MUST NOT FIRE DURING THE EXPERIMENT THAT SETS IT. The route-capacity
# check now refuses an opencode run above 22 nodes and reroutes to Sonnet - which
# would spend allowance and measure nothing, since the question here is what kimi
# actually does at 75 and 455 nodes. Lifted for these two runs only.
export ANOMALICA_OPENCODE_ENUM_LIMIT=1000000
export ANOMALICA_CLI_TIMEOUT_S=3600 ANOMALICA_CLI_LONG_TIMEOUT_S=7200
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
LOGS=/home/mark/repos/anomalica/digester/reports/kimi-nodecount-logs
mkdir -p "$LOGS"
cd /home/mark/repos/anomalica/digester/workspace
# TWO records, because one cannot separate enum size from catalogue shape.
#   invisible-college: 12 KB, 455 nodes - the extreme, but it is Vallee and
#     catalogue-shaped, as is Passport to Magonia, the book that found the
#     Claude path's own enum ceiling. A failure here alone would not tell enum
#     size apart from something particular to catalogues.
#   prime-phobos: 10 KB, 75 nodes - a scientific paper, not a catalogue, and 75
#     sits inside the untested 22-101 band, so it also narrows where the limit is.
# PHOBOS FIRST: it is the number that decides the threshold, and the flat lane
# has now been killed under us twice. Run the deciding measurement before the
# control, so an interruption costs the control rather than the answer.
for glob in "2008-pdf-the-prime-phobos-reconnaissance*" "2014-09-27-ebook-the-invisible-college*"; do
	rec=$(ls /home/mark/repos/anomalica/ingests/by-name/$glob.md 2>/dev/null | head -1)
	[ -z "$rec" ] && {
		echo "no record for $glob"
		continue
	}
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	for attempt in 1 2 3 4; do
		echo "[kimi-k3] $stem attempt $attempt $(date +%H:%M)"
		t0=$(date +%s)
		timeout 5400 python3 -m digester.cli extract "$rec" --model opencode-go/kimi-k3 \
			--digests-root /home/mark/repos/anomalica/digests --variant-only \
			>"$LOGS/$stem.log" 2>&1
		rc=$?
		el=$(($(date +%s) - t0))
		case $rc in
		0)
			echo "    ok in ${el}s - completed"
			grep -oE "^  [0-9]+ nodes" "$LOGS/$stem.log" | tail -1 | sed 's/^/      /'
			break
			;;
		77)
			echo "    paced after ${el}s; waiting 10 min"
			sleep 600
			;;
		*)
			echo "    FAILED exit $rc after ${el}s"
			tail -3 "$LOGS/$stem.log" | sed 's/^/      /'
			break
			;;
		esac
	done
done
echo "=== node-count test done $(date +%H:%M) ==="
