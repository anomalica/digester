#!/usr/bin/env bash
# Kimi K3's ceiling on long records (cleared by Mark via master, 2026-09-04):
# flat-rate opencode, variant-only, before the scheduler is allowed to hand it a
# book. Three records: a transcript with reviewer highlights so recall is
# measurable on the fixed grader, a 547 KB book, and a 260 KB transcript.
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
# Long records at an unfamiliar model's pace; the walls were chosen for Claude.
export ANOMALICA_CLI_TIMEOUT_S=3600 ANOMALICA_CLI_LONG_TIMEOUT_S=7200
export DIGESTER_ENTAILMENT=off # the scheduler's check job annotates variants
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
BY=/home/mark/repos/anomalica/ingests/by-name
LOGS=/home/mark/repos/anomalica/digester/reports/kimi-ceiling-logs
mkdir -p "$LOGS"
cd /home/mark/repos/anomalica/digester/workspace
for glob in \
	"2026-08-08-video-raymond-fowler-on-ufos*" \
	"2024-08-19-ebook-imminent*" \
	"2020-09-08-video-david-fravor-ufos-aliens*"; do
	rec=$(ls "$BY"/$glob 2>/dev/null | head -1)
	[ -z "$rec" ] && {
		echo "no record for $glob"
		continue
	}
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	for attempt in 1 2 3 4 5 6; do
		echo "[kimi-k3] $stem (attempt $attempt) $(date +%H:%M)"
		t0=$(date +%s)
		timeout 10800 python3 -m digester.cli extract "$rec" --model opencode-go/kimi-k3 \
			--digests-root /home/mark/repos/anomalica/digests --variant-only \
			>"$LOGS/$stem.log" 2>&1
		rc=$?
		el=$(($(date +%s) - t0))
		case $rc in
		0)
			echo "    ok in ${el}s"
			break
			;;
		77)
			echo "    paced after ${el}s (opencode rate limit or allowance gate); waiting 10 min"
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
echo "=== kimi ceiling runs done $(date +%H:%M) ==="
