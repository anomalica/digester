#!/usr/bin/env bash
# First-pass batch digest: run every record under ingests/by-name/
# through `digester digest`, writing .extract.md files to digests/extracts/
# and accumulating nodes/claims in the shared SQLite databases.
#
# Continues on per-record failures. Tracks succeeded/failed lists at the end.

set -u

INGESTS_DIR="/home/nonroot/ingests/by-name"
DIGESTS_DIR="/home/nonroot/digests/extracts"
LOG_FILE="/home/nonroot/digests/batch.log"
SUCCEEDED_FILE="/home/nonroot/digests/succeeded.txt"
FAILED_FILE="/home/nonroot/digests/failed.txt"

mkdir -p "${DIGESTS_DIR}"
: >"${LOG_FILE}"
: >"${SUCCEEDED_FILE}"
: >"${FAILED_FILE}"

cd /home/nonroot/workspace

mapfile -t records < <(find "${INGESTS_DIR}" -maxdepth 1 -name '*.md' -printf '%f\n' | sort)
total=${#records[@]}

echo "=== Batch digest started at $(date -Iseconds) ===" | tee -a "${LOG_FILE}"
echo "Found ${total} records" | tee -a "${LOG_FILE}"
echo "" | tee -a "${LOG_FILE}"

i=0
for record in "${records[@]}"; do
	i=$((i + 1))
	name="${record%.md}"
	input="${INGESTS_DIR}/${record}"
	output="${DIGESTS_DIR}/${name}.extract.md"

	echo "[$(date +%H:%M:%S)] [${i}/${total}] ${name}" | tee -a "${LOG_FILE}"

	start=$(date +%s)
	if python -m digester.cli digest "${input}" --output "${output}" >>"${LOG_FILE}" 2>&1; then
		elapsed=$(($(date +%s) - start))
		echo "  -> OK (${elapsed}s)" | tee -a "${LOG_FILE}"
		echo "${name}" >>"${SUCCEEDED_FILE}"
	else
		elapsed=$(($(date +%s) - start))
		echo "  -> FAILED (${elapsed}s) - see log" | tee -a "${LOG_FILE}"
		echo "${name}" >>"${FAILED_FILE}"
	fi
done

echo "" | tee -a "${LOG_FILE}"
echo "=== Batch digest finished at $(date -Iseconds) ===" | tee -a "${LOG_FILE}"
echo "Succeeded: $(wc -l <"${SUCCEEDED_FILE}")/${total}" | tee -a "${LOG_FILE}"
echo "Failed:    $(wc -l <"${FAILED_FILE}")/${total}" | tee -a "${LOG_FILE}"

python -m digester.cli stats 2>&1 | tee -a "${LOG_FILE}" || true
