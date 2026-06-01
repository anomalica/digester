VAULT_OUT := "/home/mark/repos/anomalica/generated-vault"
IMAGE := "anomalica-digester:development"

# Regenerate the Obsidian-navigable vault from the current knowledge graph.
# Wipes the output directory first to avoid stale records or nodes left over
# from prior runs.
vault:
    #!/usr/bin/env bash
    set -euo pipefail

    OUT="{{VAULT_OUT}}"

    if [[ -d "$OUT" ]]; then
        echo "Wiping existing vault at $OUT"
        trash-put "$OUT"
    fi
    mkdir -p "$OUT"

    docker run --rm \
        -v "$(pwd)/workspace:/home/nonroot/workspace" \
        -v "$OUT:/home/nonroot/vault" \
        -v "$HOME/.local/share/digester:/home/nonroot/.local/share/digester" \
        --user "$(id -u):$(id -g)" \
        -e HOME=/home/nonroot \
        -w /home/nonroot/workspace \
        {{IMAGE}} \
        python -m digester.cli export-obsidian /home/nonroot/vault

    echo ""
    echo "Vault regenerated at $OUT"

# Run the test suite inside the container.
test:
    #!/usr/bin/env bash
    set -euo pipefail
    docker run --rm \
        -v "$(pwd)/workspace:/home/nonroot/workspace" \
        --user "$(id -u):$(id -g)" \
        -w /home/nonroot/workspace \
        {{IMAGE}} \
        python -m pytest tests/ -v
