#!/usr/bin/env bash
# Effort recall test, stage 1a (cleared by Mark via master, 2026-09-02): the four
# mid-size highlighted records, Sonnet 5, low where missing (Grusch) plus medium.
# Variant-only, subscription. Entailment off: the scheduler's check job annotates
# variants afterwards, so two GPU users do not collide.
set -uo pipefail
unset DIGESTER_USE_API ANOMALICA_USE_API OPENROUTER_API_KEY
export DIGESTER_ENTAILMENT=off ANOMALICA_CLI_TIMEOUT_S=2700 ANOMALICA_CLI_LONG_TIMEOUT_S=5400
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
BY=/home/mark/repos/anomalica/ingests/by-name
LOGS=/home/mark/repos/anomalica/digester/reports/effort-stage1a-logs
mkdir -p "$LOGS"
cd /home/mark/repos/anomalica/digester/workspace
grusch=$(ls "$BY"/-video-give-us-the-authorization-david-grusch*.md | head -1)
sky=$(ls "$BY"/2026-08-09-video-ross-coulthart-q-a-skywatcher*.md | head -1)
fowler=$(ls "$BY"/2026-08-08-video-raymond-fowler-on-ufos*.md | head -1)
nolan=$(ls "$BY"/2023-12-13-video-professor-garry-nolan-ross-coulthart*.md | head -1)
run() { # effort record label
	local effort=$1 rec=$2 label=$3 stem
	stem=$(basename "$rec")
	stem=${stem%.v2.md}
	stem=${stem%.md}
	local args=(--model sonnet --digests-root /home/mark/repos/anomalica/digests --variant-only)
	[ -n "$label" ] && args+=(--run-label "$label")
	for attempt in 1 2 3 4 5 6 7 8; do
		echo "[$effort] $stem (attempt $attempt) $(date +%H:%M)"
		t0=$(date +%s)
		ANOMALICA_CLI_EFFORT=$effort timeout 7200 python3 -m digester.cli extract "$rec" "${args[@]}" >"$LOGS/$stem.$effort.log" 2>&1
		rc=$?
		el=$(($(date +%s) - t0))
		case $rc in
		0)
			echo "    ok in ${el}s"
			return 0
			;;
		77)
			echo "    allowance/rate limit after ${el}s; waiting 15 min"
			sleep 900
			;;
		*)
			echo "    FAILED exit $rc after ${el}s"
			tail -2 "$LOGS/$stem.$effort.log" | sed 's/^/      /'
			return $rc
			;;
		esac
	done
	echo "    gave up after 8 attempts"
	return 77
}
run low "$grusch" ""
run medium "$sky" effort-medium
run medium "$grusch" effort-medium
run medium "$fowler" effort-medium
run medium "$nolan" effort-medium
echo "=== stage 1a done $(date +%H:%M) ==="
