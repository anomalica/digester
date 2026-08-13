#!/usr/bin/env bash
# Canonical digests for the small reviewed-and-undigested records, smallest first.
# Direct + ungated by design: while a book runs, the scheduler's gate correctly
# holds its Claude lane, so anything left in that queue starves for the week.
set -u
cd /home/mark/repos/anomalica/digester/workspace
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
export DIGESTER_USE_API=0 ANOMALICA_CLI_EFFORT=low
LOG=/home/mark/repos/anomalica/digester/reports/batch-small.log
LIST=/home/mark/repos/anomalica/digester/reports/batch-small.list
: >"$LOG"
n=0
while read -r rec; do
	n=$((n + 1))
	echo "=== [$n] $(date -Is) $rec" >>"$LOG"
	start=$(date +%s)
	timeout 3600 python3 -m digester.cli extract \
		"/home/mark/repos/anomalica/ingests/by-name/$rec" \
		--model sonnet --digests-root /home/mark/repos/anomalica/digests >>"$LOG" 2>&1
	rc=$?
	echo "--- [$n] rc=$rc elapsed=$(($(date +%s) - start))s" >>"$LOG"
	if [ $rc -eq 77 ]; then
		echo "PARKED: plan throttled at record $n - stopping batch, cache is valid" >>"$LOG"
		exit 77
	fi
done <"$LIST"
echo "BATCH COMPLETE $(date -Is): $n records" >>"$LOG"
