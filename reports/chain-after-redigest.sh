#!/usr/bin/env bash
# Relaunch the normal (non-FORCE) queue once the FORCE re-digest run has drained.
#
# Why this exists: the video queue driver was stopped so the 16 pre-0044 re-digests
# could run without a third concurrent model user overshooting the session ceiling.
# Nothing would restart it. Relying on a driver tick to notice is the same "trigger
# on an event nobody is alive to observe" failure that once stopped the book queue
# for good - a tick that lands during the gap resumes it, a tick that does not
# leaves the pipeline idle for hours.
#
# Waits on the FORCE DRIVER's exit, not on "no extraction running": the latter is
# true in every gap BETWEEN the 16 records, so it would start the video queue
# alongside the re-digests and double the burn. The driver exits only when its
# queue is genuinely drained.
set -u
cd /home/mark/repos/anomalica/digester/reports
LOG=/home/mark/repos/anomalica/digester/reports/queue.log

# Match the process, not the environment: FORCE=1 is an env var and never appears
# in /proc/PID/cmdline, so pgrep -f 'FORCE=1' matches nothing and would fall
# straight through to launching a second driver alongside the first.
while ps -eo args --no-headers | grep -qE '^bash \./run-queue\.sh'; do
	sleep 120
done

echo "=== re-digest drained; resuming the record queue $(date -Is)" >>"$LOG"
exec setsid ./run-queue.sh
