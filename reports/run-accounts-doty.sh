#!/usr/bin/env bash
# Account pass on the Richard Doty interview (40k words, 291 highlights, ~26
# narrated accounts by master's reading). Subscription, cleared by Mark.
set -uo pipefail
unset DIGESTER_USE_API ANOMALICA_USE_API OPENROUTER_API_KEY
export ANOMALICA_CLI_TIMEOUT_S=2700 ANOMALICA_CLI_LONG_TIMEOUT_S=3000
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
cd /home/mark/repos/anomalica/digester/workspace
REC=/home/mark/repos/anomalica/ingests/by-name/-video-ex-afosi-agent-shares-wild-alien-encounters-richard-doty-ep.v2.md
DIG=/home/mark/repos/anomalica/digests/variants/-video-ex-afosi-agent-shares-wild-alien-encounters-richard-doty-ep/deepseek-deepseek-v4-flash.e9b8b6d4.yaml
OUT=/home/mark/repos/anomalica/digester/reports/accounts
for m in "${@:-sonnet}"; do
	echo "=== accounts: $m $(date +%H:%M) ==="
	timeout 3300 python3 -m digester.cli accounts "$REC" --model "$m" \
		--digest "$DIG" --out "$OUT/doty.$m.yaml" 2>&1
	echo "    exit $? $(date +%H:%M)"
done
