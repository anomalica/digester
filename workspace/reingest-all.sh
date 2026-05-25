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

# Sort records by ingest body size, smallest first. Records are symlinks into
# store/; resolve them so `ls -S` sees the actual content sizes.
mapfile -t files < <(
	find -L "$INGESTS_DIR/records" -maxdepth 1 -name '*.md' -printf '%s %p\n' |
		sort -n |
		awk '{print $2}'
)

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

	if docker run --rm \
		--name "anomalica-reingest-${name:0:30}" \
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
		python -m digester.cli extract "$ingest_container" --output "$output_container" 2>&1 |
		tail -8; then
		elapsed=$(($(date +%s) - started))
		size=$(stat -c%s "$output_host" 2>/dev/null || echo 0)
		echo "  OK  ${elapsed}s  ${size} bytes"
		ok=$((ok + 1))
	else
		echo "  FAILED"
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

# Rebuild the database from the new YAML corpus.
echo
echo "rebuilding database from new YAML corpus..."
docker run --rm \
	-v "$WORKSPACE_DIR:/home/nonroot/workspace" \
	-v "$DIGESTS_DIR:/home/nonroot/digests" \
	-v /home/mark/.local/share/digester:/home/nonroot/.local/share/digester \
	--user "$(id -u):$(id -g)" \
	-e HOME=/home/nonroot \
	-w /home/nonroot/workspace \
	anomalica-digester:development \
	bash -c 'python -m digester.cli rebuild /home/nonroot/digests/records 2>&1 | tail -4 && python -m digester.cli stats'
