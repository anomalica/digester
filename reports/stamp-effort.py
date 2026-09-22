#!/usr/bin/env python3
"""Stamp route and effort on existing digest ai_usage entries (one-off, 2026-09-02).

Every extraction before anomalica-common 04a49be ran without the artefact
saying how: on the subscription CLI at effort low (the default since the
variable existed; every script that sets it sets low), on OpenRouter or
opencode for the provider/model variants, and at medium for the three
effort-medium variants of 2026-09-02. Entries that already carry `route` are
left alone; local-stage entries (duration_s) carry neither.

    stamp-effort.py            # dry run: counts only
    stamp-effort.py --write    # rewrite the files
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, "/home/mark/repos/anomalica/anomalica-common/src")
sys.path.insert(0, "/home/mark/repos/anomalica/digester/workspace")
from anomalica_common.digest.yaml_format import _yaml_dump  # noqa: E402
from anomalica_common.llm import is_opencode_model, is_openrouter_model  # noqa: E402
from digester.health import _Loader  # noqa: E402

DIGESTS = Path("/home/mark/repos/anomalica/digests")


def route_for(model: str) -> tuple[str, str | None]:
    if is_opencode_model(model):
        return "opencode", None
    if is_openrouter_model(model):
        return "openrouter", None
    return "cli", "low"


def stamp(path: Path, write: bool) -> Counter:
    c = Counter()
    text = path.read_text()
    doc = yaml.load(text, Loader=_Loader)
    if not isinstance(doc, dict) or not isinstance(doc.get("ai_usage"), list):
        c["no_usage"] += 1
        return c
    medium = path.name.endswith(".effort-medium.yaml")
    changed = False
    for e in doc["ai_usage"]:
        if not isinstance(e, dict) or "duration_s" in e or not e.get("model"):
            c["local_or_odd"] += 1
            continue
        if e.get("route"):
            c["already"] += 1
            continue
        route, effort = route_for(str(e["model"]))
        e["route"] = route
        if effort:
            e["effort"] = "medium" if medium else effort
        c[f"{route}/{e.get('effort', '-')}"] += 1
        changed = True
    if changed and write:
        out = _yaml_dump(doc)
        yaml.load(out, Loader=_Loader)
        path.write_text(out)
        c["files_written"] += 1
    elif changed:
        c["files_to_write"] += 1
    return c


def main() -> int:
    write = "--write" in sys.argv
    paths = sorted(DIGESTS.glob("*.yaml")) + sorted(
        (DIGESTS / "variants").glob("*/*.yaml")
    )
    total = Counter()
    for p in paths:
        total += stamp(p, write)
    print(f"{len(paths)} digests scanned ({'written' if write else 'dry run'})")
    for k in sorted(total):
        print(f"  {k}: {total[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
