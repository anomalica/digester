#!/usr/bin/env python3
"""Production grader against the live workbench endpoint.

- Fetches the raw stored body + body_sha256 from the workbench.
- Maps quote text to raw-body code-point spans (timestamp/comment-aware locator).
- Seeds the Fable segment-1 draft highlights via PUT /highlights.
- Grades the segment model digests by overlap fraction and writes the
  grading/{body_sha256}.grading.json results file the workbench reads.
"""

import json
import re
import urllib.request
from pathlib import Path

import yaml

HASH = "1405206f070621abea9b5131b1512eebf13896ce9dc0ced476f4159c796c7e44"
BASE = "http://localhost:8000"
HERE = Path(__file__).resolve().parent
GRADING_DIR = Path("/home/mark/repos/anomalica/digester/grading")
RUNS = HERE / "runs"
PRECISION_THRESH = 0.8  # fraction of an item's chars that must fall inside a highlight
RECALL_THRESH = 0.5  # fraction of a highlight covered by model items to count as found

TS = re.compile(r"^\d\d:\d\d:\d\d\.\d+\s?")
COMMENT = re.compile(r"^<!--.*-->\s*$")


def searchable(body: str):
    """Lowercased content string with per-char map back to raw code-point index.
    Drops timestamp prefixes, speaker comments, markdown headers; collapses runs
    of whitespace to a single space."""
    s, idx = [], []
    prev_space = True
    for m in re.finditer(r".*?(?:\n|$)", body):
        line = m.group(0)
        base = m.start()
        if not line:
            continue
        if (
            COMMENT.match(line)
            or line.lstrip().startswith("#")
            or line.strip().startswith("*")
        ):
            continue
        off = 0
        tm = TS.match(line)
        if tm:
            off = tm.end()
        for j in range(off, len(line)):
            c = line[j]
            if c.isspace():
                if prev_space:
                    continue
                s.append(" ")
                idx.append(base + j)
                prev_space = True
            else:
                s.append(c.lower())
                idx.append(base + j)
                prev_space = False
    return "".join(s), idx


def locate(quote: str, search: str, idx: list, body: str):
    """Return raw [start,end) code-point span for a quote, or None."""
    q = re.sub(r"\s+", " ", quote.strip().lower())
    if not q:
        return None
    pos = search.find(q)
    if pos < 0:  # retry without trailing punctuation
        q2 = q.rstrip(".,;:!?\"' ")
        pos = search.find(q2)
        if pos < 0:
            return None
        q = q2
    start = idx[pos]
    end = idx[pos + len(q) - 1] + 1
    return [start, end]


def trim(span, body):
    a, b = span
    while a < b and body[a].isspace():
        a += 1
    while b > a and body[b - 1] in " \t\n.,;:!?\"'":
        b -= 1
    return [a, b]


def overlap(a, spans):
    """chars of interval a covered by the union of `spans`."""
    lo, hi = a
    cov = 0
    for s, e in sorted(spans):
        s, e = max(s, lo), min(e, hi)
        if s < e:
            cov += e - s
    return cov


def http(method, path, payload=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main():
    GRADING_DIR.mkdir(parents=True, exist_ok=True)
    b = http("GET", f"/api/ingests/{HASH}/body")
    body, sha = b["body"], b["body_sha256"]
    search, idx = searchable(body)

    # seed Fable segment-1 draft highlights
    fab = json.load(open(HERE / "fable-segment-1.json"))
    spans = []
    for c in fab.get("claims", []):
        loc = locate(c.get("quote", ""), search, idx, body)
        if loc:
            loc = trim(loc, body)
            spans.append(
                {"start": loc[0], "end": loc[1], "text": body[loc[0] : loc[1]]}
            )
    spans.sort(key=lambda s: s["start"])
    merged = []  # enforce non-overlapping
    for s in spans:
        if merged and s["start"] < merged[-1]["end"]:
            continue
        merged.append(s)
    sidecar = {
        "schema": "anomalica/highlights/1",
        "record_hash": HASH,
        "body_sha256": sha,
        "complete": False,
        "reviewed_by": "fable-draft",
        "reviewed_at": None,
        "spans": merged,
        "rejected": [],
    }
    try:
        http("PUT", f"/api/ingests/{HASH}/highlights", sidecar)
        put = f"PUT ok ({len(merged)} spans)"
    except Exception as e:  # noqa: BLE001
        put = f"PUT failed: {e}"

    hl = [(s["start"], s["end"]) for s in merged]
    hl_len = {i: (e - s) for i, (s, e) in enumerate(hl)}

    models = []
    for run in sorted(RUNS.glob("*.yaml")):
        d = yaml.safe_load(run.read_text())
        model = d.get("model", run.stem)
        items, off_target = [], []
        for c in d.get("domain_claims") or []:
            loc = locate(c.get("quote", ""), search, idx, body)
            if not loc:
                off_target.append(
                    {
                        "start": None,
                        "end": None,
                        "text": c.get("quote", "")[:120],
                        "kind": "claim",
                        "summary": c.get("text", ""),
                        "overlap_fraction": 0.0,
                    }
                )
                continue
            loc = trim(loc, body)
            length = max(1, loc[1] - loc[0])
            frac = overlap(loc, hl) / length
            items.append((loc, frac, c))
            if frac < PRECISION_THRESH:
                off_target.append(
                    {
                        "start": loc[0],
                        "end": loc[1],
                        "text": body[loc[0] : loc[1]],
                        "kind": "claim",
                        "summary": c.get("text", ""),
                        "overlap_fraction": round(frac, 3),
                    }
                )
        item_spans = [it[0] for it in items]
        # ADR 0042: highlights are casual/partial and eval-only, so the corpus-wide
        # metric is COVERAGE only (did highlighted content survive into the output).
        # Precision cannot be derived - an UNMARKED extraction is not wrong, just not
        # flagged; it is reported for inspection, never penalised. Real precision comes
        # from density-sampled section gold standards (separate).
        covered, missed = 0, []
        for i, span in enumerate(hl):
            frac = overlap(span, item_spans) / max(1, hl_len[i])
            if frac >= RECALL_THRESH:
                covered += 1
            else:
                missed.append(
                    {"start": span[0], "end": span[1], "text": body[span[0] : span[1]]}
                )
        coverage = covered / len(hl) if hl else 0.0
        models.append(
            {
                "model": model,
                "prompts": d.get("prompts"),
                "coverage": round(coverage, 3),
                "missed": missed,
                "unmarked": off_target,
            }
        )

    out = {
        "schema": "anomalica/grading/2",
        "record_hash": HASH,
        "body_sha256": sha,
        "graded_at": None,
        "models": models,
    }
    (GRADING_DIR / f"{sha}.grading.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False)
    )

    print(f"body_sha256 {sha}  |  highlights: {put}")
    print(f"\n{'model':38} {'coverage':>9}  unmarked (informational)")
    print("-" * 72)
    for m in models:
        print(f"{m['model']:38} {m['coverage']:>9.2f}  {len(m['unmarked'])}")
    print(f"\nresults: {GRADING_DIR / (sha + '.grading.json')}")


if __name__ == "__main__":
    main()
