#!/usr/bin/env bash
# Re-ingest every record in anomalica-ingests through the validated production
# prompt into native YAML at anomalica-digests/records/<name>.yaml.
#
# Runs sequentially, smallest input first so the books land last. Each record
# overwrites its old YAML in place. If a record fails the loop continues to
# the next; failures are reported at the end.
set -u

INGESTS_DIR=/home/mark/repos/anomalica/anomalica-ingests
DIGESTS_DIR=/home/mark/repos/anomalica/anomalica-digests
WORKSPACE_DIR=/home/mark/repos/anomalica/anomalica-digester/workspace

cd "$WORKSPACE_DIR"

# Max attempts per record. The Claude CLI subprocess has intermittent
# empty-stderr failures (~1 in 5 on a multi-doc batch); they succeed on a
# clean retry. This wraps each extract so a transient hiccup doesn't need a
# human.
MAX_ATTEMPTS=${MAX_ATTEMPTS:-3}

# Run one extract with retry. Captures output to a temp file (so the pipe to
# tail does NOT mask docker's real exit code - the previous version checked
# tail's exit status, which is always 0, so failures went undetected).
# Returns 0 on success, 1 if all attempts fail.
extract_with_retry() {
	local ingest_container="$1" output_container="$2" name="$3"
	local attempt rc tmplog
	tmplog=$(mktemp)
	for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
		[ "$attempt" -gt 1 ] && echo "    retry $attempt/$MAX_ATTEMPTS..."
		docker run --rm \
			--name "anomalica-reingest-${name:0:24}-${attempt}" \
			-v "$WORKSPACE_DIR:/home/nonroot/workspace" \
			-v "$INGESTS_DIR:/home/nonroot/ingests:ro" \
			-v "$DIGESTS_DIR:/home/nonroot/digests" \
			-v /home/mark/.local/share/digester:/home/nonroot/.local/share/digester \
			-v /home/mark/.local/bin/claude:/usr/local/bin/claude:ro \
			-v /home/mark/.claude:/home/nonroot/.claude \
			-v /home/mark/.claude.json:/home/nonroot/.claude.json \
			-v /tmp/digester-sandbox/empty-CLAUDE.md:/home/nonroot/.claude/CLAUDE.md:ro \
			-v /tmp/digester-sandbox/empty-settings.json:/home/nonroot/.claude/settings.json:ro \
			--user "$(id -u):$(id -g)" \
			--network host \
			-e HOME=/home/nonroot \
			-w /home/nonroot/workspace \
			anomalica-digester:development \
			python -m digester.cli extract "$ingest_container" --output "$output_container" \
			>"$tmplog" 2>&1
		rc=$?
		tail -8 "$tmplog"
		if [ "$rc" -eq 0 ]; then
			rm -f "$tmplog"
			return 0
		fi
		echo "    attempt $attempt exited $rc"
	done
	rm -f "$tmplog"
	return 1
}

# Record selection:
#   - Default: every *.md in records/, smallest input first.
#   - Scoped: set RECORD_LIST to a file of record names (one per line, with or
#     without the .md suffix). Only those records are processed. Used for the
#     PURSUE batch so we digest just the new docs, not the existing
#     UAP-disclosure records that share the records/ dir.
# Records are symlinks into store/; resolve (-L) so sizes are the real content.
if [ -n "${RECORD_LIST:-}" ]; then
	[ -f "$RECORD_LIST" ] || {
		echo "RECORD_LIST file not found: $RECORD_LIST" >&2
		exit 1
	}
	mapfile -t files < <(
		while IFS= read -r rname; do
			rname=${rname%.md}
			[ -z "$rname" ] && continue
			f="$INGESTS_DIR/records/${rname}.md"
			if [ -e "$f" ]; then
				printf '%s %s\n' "$(stat -L -c%s "$f" 2>/dev/null || echo 0)" "$f"
			else
				echo "WARNING: listed record not found, skipping: $rname" >&2
			fi
		done <"$RECORD_LIST" | sort -n | awk '{print $2}'
	)
	echo "scoped to RECORD_LIST: $RECORD_LIST"
else
	mapfile -t files < <(
		find -L "$INGESTS_DIR/records" -maxdepth 1 -name '*.md' -printf '%s %p\n' |
			sort -n |
			awk '{print $2}'
	)
fi

echo "queued ${#files[@]} records"
failed=()
ok=0

for ingest in "${files[@]}"; do
	name=$(basename "$ingest" .md)
	output_host="$DIGESTS_DIR/records/${name}.yaml"
	output_container="/home/nonroot/digests/records/${name}.yaml"
	ingest_container="/home/nonroot/ingests/records/${name}.md"

	echo
	echo "===  [$((ok + ${#failed[@]} + 1))/${#files[@]}]  $name  ==="
	started=$(date +%s)

	if extract_with_retry "$ingest_container" "$output_container" "$name"; then
		elapsed=$(($(date +%s) - started))
		size=$(stat -c%s "$output_host" 2>/dev/null || echo 0)
		echo "  OK  ${elapsed}s  ${size} bytes"
		ok=$((ok + 1))
	else
		echo "  FAILED after $MAX_ATTEMPTS attempts"
		failed+=("$name")
	fi
done

echo
echo "==========================="
echo "summary: $ok ok, ${#failed[@]} failed"
if [ ${#failed[@]} -gt 0 ]; then
	echo "failed records:"
	printf '  %s\n' "${failed[@]}"
fi

# Load the new YAMLs into the database.
#   - Default (full re-ingest): drop-and-rebuild the DB from the whole
#     digests/records corpus.
#   - Scoped (RECORD_LIST set): INCREMENTAL import of only the just-processed
#     records, so we add the PURSUE nodes to the existing graph WITHOUT
#     wiping the existing UAP-disclosure corpus.
echo
if [ -n "${RECORD_LIST:-}" ]; then
	echo "scoped run: incrementally importing ${#files[@]} record(s) (no DB rebuild)..."
	for ingest in "${files[@]}"; do
		name=$(basename "$ingest" .md)
		yaml_container="/home/nonroot/digests/records/${name}.yaml"
		[ -f "$DIGESTS_DIR/records/${name}.yaml" ] || continue
		docker run --rm \
			-v "$WORKSPACE_DIR:/home/nonroot/workspace" \
			-v "$DIGESTS_DIR:/home/nonroot/digests" \
			-v /home/mark/.local/share/digester:/home/nonroot/.local/share/digester \
			--user "$(id -u):$(id -g)" \
			-e HOME=/home/nonroot \
			-w /home/nonroot/workspace \
			anomalica-digester:development \
			python -m digester.cli import "$yaml_container" 2>&1 | tail -3
	done
	docker run --rm \
		-v "$WORKSPACE_DIR:/home/nonroot/workspace" \
		-v /home/mark/.local/share/digester:/home/nonroot/.local/share/digester \
		--user "$(id -u):$(id -g)" -e HOME=/home/nonroot -w /home/nonroot/workspace \
		anomalica-digester:development \
		python -m digester.cli stats
else
	echo "full re-ingest: rebuilding database from the whole YAML corpus..."
	docker run --rm \
		-v "$WORKSPACE_DIR:/home/nonroot/workspace" \
		-v "$DIGESTS_DIR:/home/nonroot/digests" \
		-v /home/mark/.local/share/digester:/home/nonroot/.local/share/digester \
		--user "$(id -u):$(id -g)" \
		-e HOME=/home/nonroot \
		-w /home/nonroot/workspace \
		anomalica-digester:development \
		bash -c 'python -m digester.cli rebuild /home/nonroot/digests/records 2>&1 | tail -4 && python -m digester.cli stats'
fi
