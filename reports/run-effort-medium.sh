#!/usr/bin/env bash
# Effort test (cleared 2026-09-02): three reviewed sonnet canonicals re-run at
# ANOMALICA_CLI_EFFORT=medium on the exact text their canonical was built from,
# variant-only under run label effort-medium. Subscription path, no metered spend.
set -uo pipefail
unset DIGESTER_USE_API ANOMALICA_USE_API OPENROUTER_API_KEY
export ANOMALICA_CLI_EFFORT=medium
# Walls chosen at low effort; medium runs longer per call.
export ANOMALICA_CLI_TIMEOUT_S=2700 ANOMALICA_CLI_LONG_TIMEOUT_S=5400
LOGS=/home/mark/repos/anomalica/digester/reports/effort-medium-logs
mkdir -p "$LOGS"
SNAP=/tmp/claude-1000/-home-mark-repos-anomalica-digester/0d78fe0b-75f8-4f67-acdf-50449a627092/scratchpad/effort-snapshots
cd /home/mark/repos/anomalica/digester/workspace
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
for rec in \
	"/home/mark/repos/anomalica/ingests/by-name/-audio-cosmic-top-secret-bill-hamilton-on-ufo-crash-retrievals.md" \
	"/home/mark/repos/anomalica/ingests/by-name/2022-07-31-pdf-misrep-7816710.md" \
	"$SNAP/2026-08-11-web-ic-watchdog-responds-to-alleged-uap-whistleblower-leak.md"; do
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	echo "[medium] $stem"
	t0=$(date +%s)
	timeout 7200 python3 -m digester.cli extract "$rec" --model sonnet \
		--digests-root /home/mark/repos/anomalica/digests --variant-only --run-label effort-medium \
		>"$LOGS/$stem.log" 2>&1
	rc=$?
	el=$(($(date +%s) - t0))
	case $rc in
	0) echo "    ok in ${el}s" ;;
	77)
		grep -q "Rate-limited by the Claude plan" "$LOGS/$stem.log" && echo "    THROTTLED mid-run after ${el}s" || echo "    refused before starting (allowance gate)"
		echo "    stopping"
		exit 0
		;;
	*)
		echo "    FAILED exit $rc after ${el}s"
		tail -3 "$LOGS/$stem.log" | sed 's/^/      /'
		;;
	esac
done
echo "=== effort-medium runs done ==="
