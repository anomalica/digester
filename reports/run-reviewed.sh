#!/usr/bin/env bash
# Digest a batch of records on the SUBSCRIPTION, one at a time.
#
# NO CEILING CHECK HERE, deliberately. The earlier version curl'd the usage meter
# and compared a percentage - which was FAIL-OPEN: an unreadable meter yields an
# empty string, `[ "" -ge 90 ]` errors, the `if` is false, and the batch proceeds
# exactly when nobody can see what is left. That is the failure mode the real
# guard was written to avoid, reimplemented worse in shell.
#
# `digester extract` already calls check_allowance, which fails CLOSED on an
# unreadable meter and keeps a 15-minute cached reading so a single blink does
# not stop work. It exits 77 when it refuses. So the batch reads that exit code
# instead of duplicating the logic - and 77 stops the whole batch, because a
# refusal is about the window, not about the record.
set -uo pipefail
BATCH="$1"
cd /home/mark/repos/anomalica/digester/workspace
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
n=0 ok=0 fail=0
total=$(wc -l <"$BATCH")
while read -r rec; do
	n=$((n + 1))
	echo "[$n/$total] $(basename "$rec")"
	t0=$(date +%s)
	timeout 3600 python3 -m digester.cli extract "$rec" --model sonnet \
		--digests-root /home/mark/repos/anomalica/digests >/dev/null 2>&1
	rc=$?
	el=$(($(date +%s) - t0))
	case $rc in
	0)
		ok=$((ok + 1))
		echo "    ok in ${el}s"
		;;
	77)
		echo "    REFUSED by the allowance gate - stopping the batch, $((total - n)) not started"
		echo "    (not a failure: completed chunks are cached, resume is cheap)"
		break
		;;
	75)
		echo "    cancelled at a chunk boundary after ${el}s - stopping"
		break
		;;
	*)
		fail=$((fail + 1))
		echo "    FAILED exit $rc after ${el}s"
		;;
	esac
done <"$BATCH"
echo "=== done: $ok digested, $fail failed ==="
