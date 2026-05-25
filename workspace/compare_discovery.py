"""Entity-discovery (node discovery) comparison.

Runs Pass 1 (extract every named entity in a record) four ways:

    sonnet + combined  - one prompt, all types together, iterate
    sonnet + per-type  - seven prompts, one per type, iterate each
    opus   + combined
    opus   + per-type

For each cell: iterate the model with an exclude list until it returns
fewer than MIN_NEW entities (or hits ROUND_MAX). Capture wall time, token
usage, and the full list of entities found per type.

Run via:
    docker run ... python compare_discovery.py <record-file>

Writes everything to <record-file>.discovery/ alongside the input.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from digester.record_parser import parse_record

# Iteration controls.
# The loop stops when a round returns fewer than MIN_NEW genuinely-new entities
# (dedup means re-appearing entities don't count, so this converges naturally).
# ROUND_MAX is a runaway safety ceiling ONLY - not the normal stopping mechanism.
ROUND_MAX = 40
MIN_NEW = 2

# Schemas
COMBINED_SCHEMA = {
    "type": "object",
    "required": [
        "people",
        "organisations",
        "places",
        "events",
        "matters",
        "objects",
        "documents",
        "concepts",
    ],
    "properties": {
        "extraction_complete": {"type": "boolean"},
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
        "organisations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
        "places": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
        "matters": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
    },
}

PER_TYPE_SCHEMA = {
    "type": "object",
    "required": ["entities"],
    "properties": {
        "extraction_complete": {"type": "boolean"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                },
            },
        },
    },
}

# Definitions for each node type - embedded into each prompt so the model has the rubric in context
DEFS = {
    "person": "A named human individual. 'Last, First Middle' order, no titles/ranks/honourifics/suffixes (decision 0023 + 0026). 'Dr Salvatore Pais' -> 'Pais, Salvatore'; 'Commander David Fravor' -> 'Fravor, David'. Single-name figures/pseudonyms stay as-is. Informal/short forms are aliases.",
    "organisation": "A named group: government bodies, military units, companies, programmes-as-institutions, panels, agencies, podcasts as a show, news outlets.",
    "place": "A named location specific and durable enough that a person could go to it and still find it decades later - a base, installation, research facility, ranch, named site, airport, town or city. NOT countries, states/provinces, oceans, operating areas, or broad regions (you cannot meet someone 'in America'). When a named installation is named after a geographic feature, the place is the installation ('Naval Air Station Patuxent River'), not the feature ('Patuxent River'). Name qualifying places 'Country, Region, Specific' largest-first ('USA, Nevada, Area 51'); the prefix is for sorting, the country/state is NOT itself a place node.",
    "event": "A discrete thing that happened at a specific time. MUST have a date (at least a year). If no date, it is a matter not an event.",
    "matter": "An ongoing situation, programme, investigation, or policy position that spans a period of time. AATIP-the-programme is a matter. AATIP's establishment ceremony in 2007 would be an event.",
    "object": "A specific named physical thing: craft, materials, devices, samples, sensors, weapons. Physical only. NOT documents.",
    "document": "A written or recorded artefact mentioned in the content: memo, report, letter, article, paper, book, briefing, video footage, slides, statement, testimony, affidavit, FOIA release.",
    "concept": "A RECOGNISED named idea, theory, principle, or phenomenon that exists independent of this document - one a reader could look up elsewhere (general relativity, special relativity, gravitational waves, superconductivity, zero-point energy, anti-gravity propulsion, nuclear fusion, the Pais Effect, vacuum polarisation). May be referenced without being asserted. STRICT EXCLUSIONS - NOT a concept: (a) anything touchable (that is an object; 'room temperature superconductor'=object, 'room temperature superconductivity'=concept; 'X device/reactor/craft'=object, 'X'=concept); (b) anything tied to a specific time (event/matter); (c) a person/place/organisation; (d) an effort people run over time - research, a programme, an investigation (matter); (e) a vague catch-all where almost anything fits the label ('the big secret', 'the phenomenon'); (f) jargon/mechanism from quoted patent text not recognised as a standalone idea; (g) a claimed capability or consequence; (h) an ad-hoc theory named only in this document. Merge synonyms to ONE concept.",
}

TYPES_PLURAL = {
    "person": "people",
    "organisation": "organisations",
    "place": "places",
    "event": "events",
    "matter": "matters",
    "object": "objects",
    "document": "documents",
    "concept": "concepts",
}


def _build_combined_prompt(exclude: dict[str, list[str]]) -> str:
    rules = "\n".join(f"- **{p}**: {DEFS[t]}" for t, p in TYPES_PLURAL.items())
    parts = [
        "TASK: From the document below, extract EVERY named entity. Be exhaustive - do not curate or summarise. A 300-page book contains hundreds of named entities.",
        "",
        "Categorise each entity into one of these types:",
        rules,
        "",
        "For each entity provide: `name` (canonical form per the type-specific rules) and `role` (a short one-line description of what they are / why they matter in this content).",
    ]
    has_exclude = any(exclude.get(p) for p in TYPES_PLURAL.values())
    if has_exclude:
        parts.append("")
        parts.append("ALREADY EXTRACTED - do NOT repeat any of these:")
        for plural in TYPES_PLURAL.values():
            existing = exclude.get(plural, [])
            if existing:
                parts.append(f"{plural.upper()}:")
                for name in existing:
                    parts.append(f"- {name}")
                parts.append("")
        parts.append(
            "Return ADDITIONAL entities not in the lists above. If the only entities left would be trivial, redundant, or low-confidence, do NOT pad - return empty arrays and set `extraction_complete` to true. Stopping cleanly beats marginal entries."
        )
    parts.append("")
    parts.append(
        "OUTPUT: A JSON object with `people`, `organisations`, `places`, `events`, `matters`, `objects`, `documents` arrays. Empty array for any type where you have nothing."
    )
    return "\n".join(parts)


def _build_per_type_prompt(node_type: str, exclude: list[str]) -> str:
    plural = TYPES_PLURAL[node_type]
    parts = [
        f"TASK: From the document below, extract EVERY {plural[:-1] if plural.endswith('s') else plural} mentioned. Be exhaustive - do not curate or summarise.",
        "",
        f"Definition of a **{node_type}**: {DEFS[node_type]}",
        "",
        "For each entity provide: `name` (canonical form) and `role` (a short one-line description).",
    ]
    if exclude:
        parts.append("")
        parts.append("ALREADY EXTRACTED - do NOT repeat any of these:")
        for name in exclude:
            parts.append(f"- {name}")
        parts.append("")
        parts.append(
            "Return ADDITIONAL entities not in the list above. If the only entities left would be trivial, redundant, or low-confidence, do NOT pad - return an empty array and set `extraction_complete` to true. Stopping cleanly beats marginal entries."
        )
    parts.append("")
    parts.append("OUTPUT: A JSON object with one `entities` array.")
    return "\n".join(parts)


def _claude_call(prompt: str, text_path: str, model: str, schema: dict) -> dict:
    """One claude CLI invocation. Returns wrapper dict (with structured_output, usage)."""
    full_prompt = f"{prompt}\n\nRead and analyse the document at: {text_path}"
    cmd = [
        "claude",
        "-p",
        full_prompt,
        "--model",
        model,
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
        "--tools",
        "Read",
        "--effort",
        "low",
        "--json-schema",
        json.dumps(schema),
        "--output-format",
        "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:300]}")
    return json.loads(proc.stdout)


def _round_stats(
    wrapper: dict, elapsed: float, new_count: int, extra: dict | None = None
) -> dict:
    usage = wrapper.get("usage") or {}
    info = {
        "new": new_count,
        "elapsed_s": elapsed,
        "input_tokens": usage.get("input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": wrapper.get("total_cost_usd", 0.0),
    }
    if extra:
        info.update(extra)
    return info


def run_combined(model: str, text_path: str, log, record_context: str = "") -> dict:
    """Iterate the combined prompt to exhaustion."""
    found: dict[str, list[dict]] = {p: [] for p in TYPES_PLURAL.values()}
    rounds_info = []
    total_input = total_output = total_cache_read = total_cache_creation = 0
    total_cost = 0.0
    total_elapsed = 0.0

    for round_idx in range(ROUND_MAX):
        exclude = {p: [e["name"] for e in found[p]] for p in TYPES_PLURAL.values()}
        prompt = record_context + _build_combined_prompt(exclude)
        log(f"  round {round_idx + 1}/{ROUND_MAX}", end="", flush=True)
        start = time.time()
        wrapper = _claude_call(prompt, text_path, model, COMBINED_SCHEMA)
        elapsed = time.time() - start
        structured = wrapper.get("structured_output") or {}
        new_total = 0
        for plural in TYPES_PLURAL.values():
            seen = {e["name"] for e in found[plural]}
            for entity in structured.get(plural, []):
                if entity.get("name") and entity["name"] not in seen:
                    found[plural].append(entity)
                    seen.add(entity["name"])
                    new_total += 1
        model_complete = bool(structured.get("extraction_complete", False))
        stats = _round_stats(
            wrapper,
            elapsed,
            new_total,
            {"round": round_idx + 1, "model_complete": model_complete},
        )
        total_input += stats["input_tokens"]
        total_output += stats["output_tokens"]
        total_cache_read += stats["cache_read_input_tokens"]
        total_cache_creation += stats["cache_creation_input_tokens"]
        total_cost += stats["cost_usd"]
        total_elapsed += elapsed
        rounds_info.append(stats)
        log(
            f"  +{new_total} ({elapsed:.0f}s, in={stats['input_tokens']:,}, "
            f"cache_read={stats['cache_read_input_tokens']:,}, "
            f"out={stats['output_tokens']:,})"
            f"{' [model: complete]' if model_complete else ''}"
        )
        if model_complete:
            break
        if new_total < MIN_NEW:
            break

    return {
        "strategy": "combined",
        "model": model,
        "rounds": rounds_info,
        "total_calls": len(rounds_info),
        "total_elapsed_s": total_elapsed,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_input_tokens": total_cache_read,
        "total_cache_creation_input_tokens": total_cache_creation,
        "total_cost_usd": total_cost,
        "found": found,
        "totals_per_type": {p: len(found[p]) for p in TYPES_PLURAL.values()},
    }


def run_per_type(model: str, text_path: str, log, record_context: str = "") -> dict:
    """Run separate iterative loops for each node type."""
    found_per_type: dict[str, list[dict]] = {p: [] for p in TYPES_PLURAL.values()}
    rounds_info = []
    total_input = total_output = total_cache_read = total_cache_creation = 0
    total_cost = 0.0
    total_elapsed = 0.0

    for node_type, plural in TYPES_PLURAL.items():
        log(f"  -- {plural} --")
        for round_idx in range(ROUND_MAX):
            exclude = [e["name"] for e in found_per_type[plural]]
            prompt = record_context + _build_per_type_prompt(node_type, exclude)
            log(f"    round {round_idx + 1}", end="", flush=True)
            start = time.time()
            wrapper = _claude_call(prompt, text_path, model, PER_TYPE_SCHEMA)
            elapsed = time.time() - start
            structured = wrapper.get("structured_output") or {}
            new = 0
            seen = {e["name"] for e in found_per_type[plural]}
            for entity in structured.get("entities", []):
                if entity.get("name") and entity["name"] not in seen:
                    found_per_type[plural].append(entity)
                    seen.add(entity["name"])
                    new += 1
            model_complete = bool(structured.get("extraction_complete", False))
            stats = _round_stats(
                wrapper,
                elapsed,
                new,
                {
                    "type": node_type,
                    "round": round_idx + 1,
                    "model_complete": model_complete,
                },
            )
            total_input += stats["input_tokens"]
            total_output += stats["output_tokens"]
            total_cache_read += stats["cache_read_input_tokens"]
            total_cache_creation += stats["cache_creation_input_tokens"]
            total_cost += stats["cost_usd"]
            total_elapsed += elapsed
            rounds_info.append(stats)
            log(
                f"  +{new} ({elapsed:.0f}s, in={stats['input_tokens']:,}, "
                f"cache_read={stats['cache_read_input_tokens']:,}, "
                f"out={stats['output_tokens']:,})"
                f"{' [model: complete]' if model_complete else ''}"
            )
            if model_complete:
                break
            if new < MIN_NEW:
                break

    return {
        "strategy": "per_type",
        "model": model,
        "rounds": rounds_info,
        "total_calls": len(rounds_info),
        "total_elapsed_s": total_elapsed,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_input_tokens": total_cache_read,
        "total_cache_creation_input_tokens": total_cache_creation,
        "total_cost_usd": total_cost,
        "found": found_per_type,
        "totals_per_type": {p: len(found_per_type[p]) for p in TYPES_PLURAL.values()},
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: compare_discovery.py <record-file>", file=sys.stderr)
        sys.exit(1)
    record_path = Path(sys.argv[1])
    record_text = record_path.read_text()
    parsed = parse_record(record_text)
    body = parsed.body
    from digester.extract import build_record_context

    record_context = build_record_context(
        title=parsed.title,
        authors=parsed.authors,
        date=parsed.date,
        source_type=parsed.source_type,
    )
    print(f"Input: {record_path} (body {len(body):,} chars)")
    print(f"Record context:\n{record_context}")

    # Write the body to a temp file for the Read tool
    fd, body_path = tempfile.mkstemp(suffix=".txt", prefix="discovery-")
    with os.fdopen(fd, "w") as f:
        f.write(body)

    out_dir = record_path.parent / f"{record_path.stem}.discovery"
    out_dir.mkdir(exist_ok=True)

    results = []
    try:
        for model in ("sonnet", "opus"):
            for strategy in ("combined", "per_type"):
                print(f"\n=== {model.upper()} / {strategy.upper()} ===", flush=True)
                run_fn = run_combined if strategy == "combined" else run_per_type
                t_start = time.time()
                try:

                    def _log(*a, **k):
                        k.setdefault("flush", True)
                        print(*a, **k)

                    result = run_fn(
                        model, body_path, log=_log, record_context=record_context
                    )
                except Exception as e:
                    result = {"strategy": strategy, "model": model, "error": str(e)}
                t_total = time.time() - t_start
                print(f"=== done in {t_total:.0f}s ===")
                results.append(result)
                out_file = out_dir / f"{model}-{strategy}.json"
                out_file.write_text(json.dumps(result, indent=2))
                print(f"saved {out_file}")
    finally:
        os.unlink(body_path)

    print("\n\n=== SUMMARY ===")
    print(
        f"{'CELL':<22} {'CALLS':>6} {'TIME':>8} {'INPUT':>12} {'CACHE_RD':>12} "
        f"{'CACHE_WR':>12} {'OUTPUT':>10} {'COST':>10}"
    )
    for r in results:
        if "error" in r:
            print(f"{r['model'] + '/' + r['strategy']:<22} ERROR: {r['error'][:60]}")
            continue
        cell = f"{r['model']}/{r['strategy']}"
        per = r.get("totals_per_type", {})
        print(
            f"{cell:<22} {r['total_calls']:>6} {r['total_elapsed_s']:>7.0f}s "
            f"{r['total_input_tokens']:>12,} "
            f"{r.get('total_cache_read_input_tokens', 0):>12,} "
            f"{r.get('total_cache_creation_input_tokens', 0):>12,} "
            f"{r['total_output_tokens']:>10,} "
            f"${r['total_cost_usd']:>9.4f}"
        )
        print(f"    per-type: {per}")

    # Per-type "found" union summary
    print("\n=== UNIQUE NAMES per type (across all 4 cells, for coverage check) ===")
    union_per_type: dict[str, set[str]] = {p: set() for p in TYPES_PLURAL.values()}
    for r in results:
        if "error" in r:
            continue
        for plural, lst in r["found"].items():
            for e in lst:
                if e.get("name"):
                    union_per_type[plural].add(e["name"])
    for plural, names in union_per_type.items():
        print(f"  {plural}: {len(names)} unique across all cells")

    (out_dir / "summary.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
