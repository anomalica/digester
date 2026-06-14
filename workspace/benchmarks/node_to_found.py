#!/usr/bin/env python3
"""Convert a digest YAML's nodes into the benchmark runner's extraction.json
format: {"found": {type: [{"name": ...}]}}. Usage: node_to_found.py <digest.yaml>"""

import json
import sys

import yaml

d = yaml.safe_load(open(sys.argv[1]))
found = {}
for n in d.get("nodes", []) or []:
    t = n.get("type") or n.get("node_type") or "unknown"
    found.setdefault(t, []).append({"name": n.get("name", "")})
print(json.dumps({"found": found}))
