#!/usr/bin/env bash
# Build a minimal, isolated Claude home for extraction runs, and echo its path.
#
# The digester drives `claude -p` inside a container. Mounting the real ~/.claude
# into it hands the extraction model the operator's personal CLAUDE.md - 34,000
# characters of development guidelines that have nothing to do with reading a
# transcript. Two costs, one of them not obvious:
#
#   TOKENS  - it is loaded on EVERY call. Measured on a 10-token question:
#             14,121 tokens of overhead with it, 6,946 without. Half the fixed
#             cost of every extraction call was the operator's dotfiles.
#
#   BEHAVIOUR - worse, the model OBEYS it. The personal guidelines say "British
#             English everywhere", and the extraction duly wrote "Defence
#             Intelligence Agency" for a US federal agency whose name is spelled
#             "Defense". A style rule meant for the operator's own prose silently
#             corrupted the name of an entity in the knowledge graph.
#
# Only the credentials are needed to authenticate. Nothing else comes along.
set -euo pipefail

SRC="${HOME}/.claude"
DEST="${XDG_RUNTIME_DIR:-/tmp}/anomalica-claude-home"

rm -rf "$DEST"
mkdir -p "$DEST"
chmod 700 "$DEST"

if [[ ! -f "${SRC}/.credentials.json" ]]; then
	echo "No ${SRC}/.credentials.json - run 'claude' once to log in." >&2
	exit 1
fi
cp "${SRC}/.credentials.json" "${DEST}/.credentials.json"
chmod 600 "${DEST}/.credentials.json"

echo "$DEST"
