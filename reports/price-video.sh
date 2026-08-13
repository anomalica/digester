#!/usr/bin/env bash
set -u
cd /home/mark/repos/anomalica/digester/workspace
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
export DIGESTER_USE_API=0 ANOMALICA_CLI_EFFORT=low
export ANOMALICA_CLI_TIMEOUT_S=1800 ANOMALICA_CLI_LONG_TIMEOUT_S=3600
LOG=/home/mark/repos/anomalica/digester/reports/video-price.log
REC=$(head -1 /home/mark/repos/anomalica/digester/reports/video-price.list)
start=$(date +%s)
echo "=== $(date -Is) $REC" >"$LOG"
timeout 21600 python3 -m digester.cli extract \
	"/home/mark/repos/anomalica/ingests/by-name/$REC" \
	--model sonnet --digests-root /home/mark/repos/anomalica/digests >>"$LOG" 2>&1
echo "--- rc=$? elapsed=$(($(date +%s) - start))s" >>"$LOG"
