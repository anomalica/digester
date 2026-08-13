#!/usr/bin/env bash
# Successor to run-books.sh. Works the SAME books.list, but survives the failure
# that made the 2026-08-01 04:07 queue edit inert:
#
#   run-books.sh reads the queue ONCE with mapfile, deliberately - a `while read
#   < file` loop holds an fd whose byte offset survives the file being rewritten,
#   which once made it skip five books and run one that had been removed. The fix
#   was right, but it has a consequence nobody stated: a queue edited AFTER the
#   loop starts has no effect at all. The list was rewritten from 8 books to 130
#   records fourteen hours into the run, and the running process never saw one of
#   them. It would have printed COMPLETE and exited on a queue it had never read.
#
# So: re-read the list at the top of EVERY iteration (fresh mapfile, no held fd),
# and derive what is left from what is ON DISK rather than from a cursor. There is
# no position to get out of step with the file.
set -u
cd /home/mark/repos/anomalica/digester/workspace
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
export DIGESTER_USE_API=0 ANOMALICA_CLI_EFFORT=low
export ANOMALICA_CLI_TIMEOUT_S=1800 ANOMALICA_CLI_LONG_TIMEOUT_S=3600

RECORDS=/home/mark/repos/anomalica/ingests/by-name
DIGESTS=/home/mark/repos/anomalica/digests
LIST=${LIST:-/home/mark/repos/anomalica/digester/reports/books.list}
LOG=${LOG:-/home/mark/repos/anomalica/digester/reports/queue.log}
PARK_SLEEP_S=${PARK_SLEEP_S:-600}
MAX_STRIKES=${MAX_STRIKES:-2}
# FORCE=1 re-digests records that ALREADY have a digest. Off by default, because
# the normal queue must never re-run finished work - but a re-digest is the whole
# point when the existing digest predates a prompt change. The canonical in
# records/ is REPLACED (verified in code and empirically by the digester); the 16
# pre-0044 targets carry `prompts: None` so they have no variant on disk, meaning
# the canonical is the only copy and git is the sole pre-state. Commit first.
FORCE=${FORCE:-0}

declare -A strikes
declare -A done_force
# Records refused by the ALLOWANCE gate (rc=77) this pass. They are not failures
# and must not take a strike - but they must be SKIPPED OVER rather than parked on.
# The gate is size-dependent: a book-scale record stops at 88% weekly while a small
# one stops at 93, so at 89% the head of the queue can be permanently refused while
# records behind it would run. Parking on the head blocks them all - the identical
# head-of-line failure the scheduler had already fixed for metered variants, and I
# rebuilt it here. Park only when NOTHING is dispatchable.
declare -A deferred

# One extraction at a time, machine-wide. Wait on the EXTRACTION, not on the old
# driver's name: run-books.sh is being retired, and killing its shell orphans a
# live extract onto init. Keying the guard to the process name would have seen no
# driver, started a second extraction of the same book, and doubled the burn
# against a ceiling sized for one.
while pgrep -f 'python3 -m digester.cli extract' >/dev/null 2>&1; do
	echo "$(date -Is) waiting: an extraction is already running" >>"$LOG"
	sleep 120
done
echo "=== queue driver up $(date -Is)" >>"$LOG"

# The digest for record X.v2.md is X.yaml - the .v2 is stripped on the digest side
# and only the symlink joins the two namespaces. Getting this wrong reads every
# record as undigested and re-runs the entire corpus.
digest_for() {
	local slug="${1%.md}"
	echo "$DIGESTS/records/${slug%.v2}.yaml"
}

while :; do
	mapfile -t QUEUE <"$LIST"
	next=""
	for rec in "${QUEUE[@]}"; do
		[ -n "$rec" ] || continue
		if [ "$FORCE" != "1" ] && [ -f "$(digest_for "$rec")" ]; then continue; fi
		# In FORCE mode the digest always exists, so "already done" cannot be the
		# skip signal. Track completions in-run instead, keyed by the record name.
		# FORCE completion must be derived from the ARTEFACT, not from memory. The
		# in-memory map alone made a restart re-run every finished record: the
		# relaunch picked Nimitz, already re-digested, and burned allowance redoing
		# it. A digest that predates ADR 0044 carries no provenance_chain at all and
		# a re-digest gives it one on essentially every claim, so the presence of the
		# key IS the goal state - the same "derive what remains from what is on disk"
		# rule that the queue itself follows.
		if [ "$FORCE" = "1" ] && grep -qm1 'provenance_chain' "$(digest_for "$rec")" 2>/dev/null; then continue; fi
		if [ "$FORCE" = "1" ] && [ -n "${done_force[$rec]:-}" ]; then continue; fi
		[ -n "${deferred[$rec]:-}" ] && continue
		[ "${strikes[$rec]:-0}" -ge "$MAX_STRIKES" ] && continue
		next="$rec"
		break
	done

	if [ -z "$next" ]; then
		# Nothing dispatchable. If any record was merely allowance-deferred rather
		# than done, this is a park, not a drain - clear the deferrals and re-probe
		# after the sleep. Only an empty deferred set means genuinely finished.
		if [ "${#deferred[@]}" -gt 0 ]; then
			echo "    all remaining deferred by allowance; re-check in ${PARK_SLEEP_S}s" >>"$LOG"
			sleep "$PARK_SLEEP_S"
			deferred=()
			continue
		fi
		echo "QUEUE DRAINED $(date -Is)" >>"$LOG"
		break
	fi

	echo "=== $(date -Is) $next" >>"$LOG"
	start=$(date +%s)
	timeout 21600 python3 -m digester.cli extract "$RECORDS/$next" \
		--model sonnet --digests-root "$DIGESTS" >>"$LOG" 2>&1
	rc=$?
	echo "--- rc=$rc elapsed=$(($(date +%s) - start))s $next" >>"$LOG"

	# 77 is the allowance gate, not a fault, and never a strike. Never EXIT on it -
	# "resume when the window rolls" needs a process alive to observe the roll, and
	# exiting is how the queue stopped for good last time. Defer and move on; the
	# park happens above, only once nothing at all is dispatchable.
	if [ "$rc" -eq 77 ]; then
		# Refused by the allowance gate. Defer THIS record and try the next one -
		# the gate is size-dependent, so a record behind it may still be eligible.
		deferred[$next]=1
		echo "    deferred (allowance): $next" >>"$LOG"
		# ...but the NON-BOOK line is the most permissive one there is. If a record
		# under BOOK_SCALE_CHARS was refused, nothing smaller can pass either, so
		# probing the rest of the queue cannot find work - it only costs a gate call
		# per record, and each of those SSHes to Forest against an endpoint already
		# answering in 8-29s. At the stop line that is ~96 polls every pass, for the
		# four days until the window rolls. Defer the remainder in one go instead.
		body=$(wc -c <"$RECORDS/$next" 2>/dev/null || echo 0)
		if [ "$body" -lt 200000 ]; then
			echo "    non-book refused - nothing smaller can pass; parking early" >>"$LOG"
			for rest in "${QUEUE[@]}"; do
				[ -n "$rest" ] && deferred[$rest]=1
			done
		fi
		continue
	fi

	# A genuine failure gets a bounded number of attempts, then the queue moves on.
	# Unbounded retry once burned 69 attempts at ~30 minutes on a record the model
	# could not process, and nothing said stop.
	if [ "$rc" -eq 0 ] && [ "$FORCE" = "1" ]; then
		done_force[$next]=1
	fi

	if [ "$rc" -ne 0 ]; then
		strikes[$next]=$((${strikes[$next]:-0} + 1))
		echo "    strike ${strikes[$next]}/$MAX_STRIKES on $next" >>"$LOG"
	fi
done
