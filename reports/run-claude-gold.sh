#!/usr/bin/env bash
# Claude tiers across the gold records, on the SUBSCRIPTION (no metered dollars).
# Variant-only: these are comparison runs and must never become canonical.
# The CLI's own allowance gate decides whether each may start; 77 stops the batch.
set -uo pipefail
# Env hygiene: an inherited DIGESTER_USE_API/ANOMALICA_USE_API makes the spend
# gate treat a subscription alias as metered and refuse with exit 2 at 0s, which
# reads as a model failure. A batch must not inherit the transport decision from
# whatever shell launched it.
unset DIGESTER_USE_API ANOMALICA_USE_API OPENROUTER_API_KEY
# Per-run logs, NEVER /dev/null. Discarded output has now cost three separate
# diagnoses today: a failure with no message forces guessing, and I guessed the
# spend gate and then inherited env vars before discovering the real cause was a
# relative path that did not resolve after the cd. The exit code alone said 2,
# which is click's invalid-value code and not the spend gate it looks like.
LOGS=/home/mark/repos/anomalica/digester/reports/claude-gold-logs
mkdir -p "$LOGS"
# Paths must be ABSOLUTE: this script cds, so a relative path in the batch file
# resolves against the wrong root and every record fails identically at 0s.
while read -r line; do case "$line" in /*) ;; *)
	echo "batch paths must be absolute: $line" >&2
	exit 1
	;;
esac done <"$1"
cd /home/mark/repos/anomalica/digester/workspace
export PYTHONPATH=/home/mark/repos/anomalica/anomalica-common/src:.
for rec in $(cat "$1"); do
	for m in haiku sonnet opus; do
		stem=$(basename "$rec")
		stem=${stem%.v2.md}
		stem=${stem%.md}
		if compgen -G "/home/mark/repos/anomalica/digests/variants/$stem/$m.*.yaml" >/dev/null; then
			echo "[$m] $stem - already done, skipping"
			continue
		fi
		echo "[$m] $stem"
		t0=$(date +%s)
		timeout 5400 python3 -m digester.cli extract "$rec" --model "$m" \
			--digests-root /home/mark/repos/anomalica/digests --variant-only \
			>"$LOGS/$stem.$m.log" 2>&1
		rc=$?
		el=$(($(date +%s) - t0))
		case $rc in
		0) echo "    ok in ${el}s" ;;
		77) # 77 is BOTH a pre-flight allowance refusal and a mid-run plan throttle.
			# They look identical in the exit code and are not the same event: one
			# never started, the other did real work and was cut off. Read the log.
			if grep -q "Rate-limited by the Claude plan" "$LOGS/$stem.$m.log"; then
				echo "    THROTTLED mid-run after ${el}s - chunks cached, resume is cheap"
			else
				echo "    refused before starting (allowance gate)"
			fi
			echo "    stopping the batch; rerun after the window rolls"
			exit 0
			;;
		*)
			echo "    FAILED exit $rc after ${el}s"
			sed -n '$p' "$LOGS/$stem.$m.log" | sed 's/^/      /'
			;;
		esac
	done
done
echo "=== claude gold sweep done ==="
