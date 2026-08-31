#!/usr/bin/env python3
"""Grade a model sweep on three axes that do not need ground truth.

We have no gold for this record. Highlights are NOT gold - they are a reviewer
saying "this matters, make sure you get it", which is a RECALL TARGET and
nothing more. A model can score 100% here and still be wrong about everything
unhighlighted, so read coverage as a floor, never as accuracy.

Everything is measured in ONE frame. Highlight text and claim quotes are both
located inside the materialised pre-digest via eval.searchable/locate, so no
offset is ever carried between the body frame and the materialised frame - that
mismatch resolves spans onto the wrong text rather than failing, which looks
like it worked.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from anomalica_common.pre_digest import materialise  # noqa: E402

from digester.eval import locate, searchable  # noqa: E402
from digester.record_parser import parse_record  # noqa: E402

# A claim asserting several things at once is not atomic. These are the joins
# that carry a second assertion rather than a compound noun phrase.
_COMPOUND = re.compile(
    r",\s+(?:and|but|while|whereas)\s+\w+|;\s|\band also\b|\bas well as\b", re.I
)


def highlights(body: str) -> list[str]:
    """Highlighted passages, flattened. Markers nest, so the inner start tag of
    an overlapping pair would otherwise be captured as part of the outer text."""
    # Group by ID, not per pair. A highlight may be EXTENDED - one id, several
    # start/end pairs - so counting pairs inflates the coverage DENOMINATOR and
    # every model's score drops for a reviewer choosing two ends of a paragraph
    # over the whole thing. One highlight is one expected claim however many
    # pieces its evidence arrives in.
    by_id: dict[str, list[str]] = {}
    seq = 0
    for m in re.finditer(
        r"\{\{highlight-start:?\s*([A-Za-z0-9]*)[^}]*\}\}(.*?)"
        r"\{\{highlight-end[^}]*\}\}",
        body,
        re.S,
    ):
        hid = m.group(1) or f"_anon{(seq := seq + 1)}"
        by_id.setdefault(hid, []).append(m.group(2))
    out = []
    for hid, chunks in by_id.items():
        m_group = " [...] ".join(chunks)
        t = re.sub(r"\{\{[^}]*\}\}", " ", m_group)
        t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) > 40:
            out.append(t)
    return out


def spans_in(texts: list[str], content: str, idx: list[int]) -> list[tuple[int, int]]:
    out = []
    for t in texts:
        pos = locate(t, content, idx)
        if pos:
            out.append((pos[0], pos[-1]))
    return out


def grade(digest_path: Path, body: str) -> dict:
    d = yaml.safe_load(digest_path.read_text()) or {}
    claims = (d.get("domain_claims") or []) + (d.get("infrastructure_claims") or [])
    mat = materialise(body)
    content, idx = searchable(mat)

    hl = highlights(body)
    hl_spans = spans_in(hl, content, idx)
    quotes = [(c.get("quote") or "").strip() for c in claims]
    q_spans = spans_in([q for q in quotes if q], content, idx)

    covered = sum(
        1 for a, b in hl_spans if any(not (qb < a or qa > b) for qa, qb in q_spans)
    )
    texts = [(c.get("claim") or c.get("text") or "") for c in claims]
    lens = [len(t) for t in texts if t]
    return {
        "model": digest_path.stem.split(".", 1)[-1].replace("_", "/"),
        "claims": len(claims),
        "nodes": len(d.get("nodes") or []),
        "highlights_found": len(hl_spans),
        "highlights_covered": covered,
        "coverage": covered / len(hl_spans) if hl_spans else None,
        # a quote that cannot be located is not in the source it cites
        "quotes": len([q for q in quotes if q]),
        "quotes_located": len(q_spans),
        "fidelity": len(q_spans) / max(1, len([q for q in quotes if q])),
        "mean_claim_chars": round(sum(lens) / len(lens)) if lens else 0,
        "compound_pct": round(
            100 * sum(1 for t in texts if _COMPOUND.search(t)) / max(1, len(texts))
        ),
    }


def main() -> None:
    record = Path(sys.argv[1])
    runs = sorted(Path(sys.argv[2]).glob("*.yaml"))
    body = parse_record(record.read_text()).body
    rows = []
    for r in runs:
        if r.name == "manifest.json":
            continue
        try:
            rows.append(grade(r, body))
        except Exception as e:  # a broken run is a result, not a crash
            rows.append({"model": r.stem, "error": str(e)[:60]})
    rows.sort(key=lambda x: (-(x.get("coverage") or 0), -(x.get("fidelity") or 0)))
    hdr = f"{'model':42} {'clm':>4} {'node':>5} {'cover':>6} {'fidel':>6} {'chars':>6} {'cmpd':>5}"
    print(hdr)
    print("-" * len(hdr))
    for x in rows:
        if x.get("error"):
            print(f"{x['model']:42} ERROR {x['error']}")
            continue
        cov = f"{x['coverage'] * 100:.0f}%" if x["coverage"] is not None else "-"
        print(
            f"{x['model']:42} {x['claims']:4} {x['nodes']:5} {cov:>6} "
            f"{x['fidelity'] * 100:5.0f}% {x['mean_claim_chars']:6} {x['compound_pct']:4}%"
        )
    import json

    Path(sys.argv[3]).write_text(json.dumps(rows, indent=2)) if len(
        sys.argv
    ) > 3 else None


if __name__ == "__main__":
    main()
