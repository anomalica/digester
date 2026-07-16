#!/usr/bin/env python3
"""Align N per-model digests of one record and render them for human judgement.

THE PROBLEM THIS SOLVES. Comparing models by reading their digests end to end is
impossible: opus emits ~400 claims for a 3.5-hour record and haiku ~250, and a
reviewer cannot hold two lists of that size side by side. So the comparison has to
be reduced to the DISAGREEMENTS, and someone has to decide which model won.

THE ALIGNMENT KEY IS THE TIMECODE. Every claim carries a location recovered
deterministically from its verbatim quote (realign.py), so two models describing
the same moment land on the same span whatever they named the entities in it. That
is what makes a cross-model comparison possible at all: nothing else about two
models' output lines up - not node names, not source ids, not claim wording.

WHAT THE REVIEWER SEES. Claims are bucketed into passages. A passage where every
model said the same thing is agreement - collapsed, because there is nothing to
judge. A passage where the models diverge, or where only one of them found
anything, is expanded. That is the signal, and it is a small fraction of the
whole: the reviewer judges the deltas, never the corpus.
"""

from __future__ import annotations

import argparse
import html
import re
from difflib import SequenceMatcher
from pathlib import Path

import yaml

_TC = re.compile(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d))?")

# Claims from different models are candidates for the same passage when their
# spans overlap at all. Transcript claims are short and the realigner is exact, so
# overlap is a strong signal; text similarity then decides agreement vs divergence.
_AGREE_SIMILARITY = 0.55


def _seconds(tc: str) -> float | None:
    m = _TC.search(tc or "")
    if not m:
        return None
    h, mi, s, d = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + (int(d) / 10 if d else 0)


def _span(location: str) -> tuple[float, float] | None:
    """(start, end) seconds from an 'HH:MM:SS.d-HH:MM:SS.d' location."""
    if not location:
        return None
    parts = _TC.findall(location)
    if not parts:
        return None
    times = []
    for h, mi, s, d in parts:
        times.append(int(h) * 3600 + int(mi) * 60 + int(s) + (int(d) / 10 if d else 0))
    return (times[0], times[-1] if len(times) > 1 else times[0])


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def load_digest(path: Path) -> dict:
    """Read a digest YAML into {model, claims[]} with parsed spans."""
    d = yaml.safe_load(path.read_text())
    claims = (d.get("domain_claims") or []) + (d.get("infrastructure_claims") or [])
    out = []
    for c in claims:
        sp = _span(str(c.get("location") or ""))
        if not sp:
            continue
        out.append(
            {
                "start": sp[0],
                "end": sp[1],
                "text": c.get("text") or c.get("content") or "",
                "quote": c.get("quote") or c.get("original_excerpt") or "",
                "type": c.get("type") or c.get("claim_type") or "",
                "attestation": c.get("attestation"),
                "chain": c.get("provenance_chain"),
                "refs": [
                    r.get("name") if isinstance(r, dict) else r
                    for r in (c.get("refs") or c.get("node_references") or [])
                ],
            }
        )
    out.sort(key=lambda c: c["start"])
    return {"model": d.get("model") or path.stem, "claims": out}


def align(digests: list[dict], window: float = 12.0) -> list[dict]:
    """Bucket claims from every model into passages by overlapping timecode.

    A passage is a moment in the record. Each model either said something about it
    or did not - and "did not" is exactly as interesting as what it said.
    """
    events = []
    for di, d in enumerate(digests):
        for c in d["claims"]:
            events.append((c["start"], di, c))
    events.sort(key=lambda e: e[0])

    passages: list[dict] = []
    for start, di, claim in events:
        placed = False
        for p in reversed(passages[-8:]):  # only recent passages can still be open
            if start - p["start"] <= window:
                p["by_model"].setdefault(di, []).append(claim)
                p["end"] = max(p["end"], claim["end"])
                placed = True
                break
        if not placed:
            passages.append(
                {
                    "start": start,
                    "end": claim["end"],
                    "by_model": {di: [claim]},
                }
            )

    for p in passages:
        found = set(p["by_model"])
        p["found_by"] = found
        p["missing"] = set(range(len(digests))) - found
        # Agreement = every model present, and their leading claims say the same thing.
        if p["missing"]:
            p["verdict"] = "divergent"
        else:
            texts = [v[0]["text"] for v in p["by_model"].values()]
            worst = min(
                (
                    _similar(texts[i], texts[j])
                    for i in range(len(texts))
                    for j in range(i + 1, len(texts))
                ),
                default=1.0,
            )
            p["verdict"] = "agreed" if worst >= _AGREE_SIMILARITY else "divergent"
    return passages


def _fmt(sec: float) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def render(digests: list[dict], passages: list[dict], out: Path, record: str) -> None:
    names = [d["model"] for d in digests]
    agreed = sum(1 for p in passages if p["verdict"] == "agreed")
    divergent = [p for p in passages if p["verdict"] == "divergent"]
    e = html.escape

    def cell(p, di):
        cs = p["by_model"].get(di)
        if not cs:
            return '<td class="miss">&mdash; nothing &mdash;</td>'
        body = []
        for c in cs[:3]:
            chain = c.get("chain") or {}
            frame = f'<span class="pill t-{e(c["type"])}">{e(c["type"])}</span>'
            if c.get("attestation"):
                frame += f'<span class="att">{e(c["attestation"])}</span>'
            if chain.get("origin_kind"):
                frame += f'<span class="att ok">{e(chain["origin_kind"])}</span>'
            body.append(f'<div class="c">{frame}<p>{e(c["text"])}</p></div>')
        if len(cs) > 3:
            body.append(f'<div class="more">+{len(cs) - 3} more</div>')
        return f"<td>{''.join(body)}</td>"

    rows = []
    for i, p in enumerate(divergent):
        cells = "".join(cell(p, di) for di in range(len(digests)))
        rows.append(
            f'<tr><td class="tc">{_fmt(p["start"])}</td>{cells}'
            f'<td class="judge"><button data-p="{i}" data-v="a">A</button>'
            f'<button data-p="{i}" data-v="b">B</button>'
            f'<button data-p="{i}" data-v="n">neither</button></td></tr>'
        )

    heads = "".join(f"<th>{e(n)}</th>" for n in names)
    doc = f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model comparison &mdash; {e(record)}</title>
<style>
*,*::before,*::after{{box-sizing:border-box}} body,h1,h2,p,table{{margin:0}}
:root{{--ink:#14171b;--soft:#4a525c;--faint:#7c8590;--paper:#f5f6f4;--panel:#fff;
--rule:#dcdfda;--steel:#3d5a80;--good:#2c6a52;--good-bg:#e8f2ec;--bad:#a8362a;--warn:#8a6212;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;--sans:ui-sans-serif,system-ui,"Segoe UI",Helvetica,Arial,sans-serif}}
@media(prefers-color-scheme:dark){{:root{{--ink:#e6e9ec;--soft:#a2acb6;--faint:#6d7885;
--paper:#101317;--panel:#171b21;--rule:#2a3138;--steel:#8ba9d0;--good:#79c2a1;--good-bg:#13241d;
--bad:#e08578;--warn:#d6a94e}}}}
body{{background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.5}}
.wrap{{max-width:1500px;margin:0 auto;padding:2.5rem 1.25rem 5rem;display:flex;flex-direction:column;gap:1.5rem}}
.eyebrow{{font-family:var(--mono);font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:var(--faint)}}
h1{{font-size:1.8rem;font-weight:640;letter-spacing:-.02em;margin:.4rem 0 .6rem}}
p{{color:var(--soft);max-width:76ch}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.6rem}}
.kpi{{background:var(--panel);border:1px solid var(--rule);padding:.7rem .85rem}}
.kpi .n{{font-family:var(--mono);font-size:1.4rem;font-weight:600;font-variant-numeric:tabular-nums}}
.kpi .l{{font-size:.72rem;color:var(--faint);font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em}}
.kpi.good .n{{color:var(--good)}}
.note{{border-left:3px solid var(--good);background:var(--good-bg);padding:.8rem 1rem;font-size:.9rem;color:var(--soft)}}
.tw{{overflow-x:auto;border:1px solid var(--rule);background:var(--panel)}}
table{{border-collapse:collapse;width:100%;font-size:.84rem}}
th{{text-align:left;padding:.55rem .7rem;border-bottom:1px solid var(--rule);font-family:var(--mono);
font-size:.66rem;letter-spacing:.09em;text-transform:uppercase;color:var(--faint);font-weight:500;
position:sticky;top:0;background:var(--panel)}}
td{{padding:.6rem .7rem;border-bottom:1px solid var(--rule);vertical-align:top;color:var(--soft)}}
td.tc{{font-family:var(--mono);font-size:.75rem;color:var(--faint);white-space:nowrap}}
td.miss{{color:var(--bad);font-style:italic;font-size:.8rem}}
.c{{margin-bottom:.5rem}} .c:last-child{{margin-bottom:0}}
.c p{{margin:.2rem 0 0;color:var(--ink);font-size:.83rem}}
.pill{{font-family:var(--mono);font-size:.63rem;padding:.1rem .32rem;border:1px solid currentColor;margin-right:.3rem}}
.t-hearsay{{color:var(--bad)}} .t-opinion{{color:var(--warn)}} .t-testimony{{color:var(--steel)}}
.t-observation{{color:var(--good)}} .t-administrative{{color:var(--faint)}}
.att{{font-family:var(--mono);font-size:.63rem;color:var(--faint);margin-right:.3rem}}
.att.ok{{color:var(--good)}}
.more{{font-size:.72rem;color:var(--faint);font-family:var(--mono)}}
.judge{{white-space:nowrap}}
.judge button{{font-family:var(--mono);font-size:.7rem;padding:.25rem .5rem;margin-right:.2rem;
border:1px solid var(--rule);background:transparent;color:var(--soft);cursor:pointer}}
.judge button[aria-pressed=true]{{background:var(--good-bg);color:var(--good);border-color:var(--good);font-weight:700}}
.judge button:focus-visible{{outline:2px solid var(--steel);outline-offset:1px}}
</style></head><body><div class="wrap">
<header>
  <p class="eyebrow">Anomalica &middot; digester &middot; model comparison</p>
  <h1>{e(record)}</h1>
  <p>Claims from each model, aligned by the timecode recovered from their verbatim quotes.
  Passages where the models <strong>agree</strong> are hidden &mdash; there is nothing to judge.
  What is left is where they <strong>diverge</strong>, or where only one model found anything.</p>
</header>
<div class="kpis">
  <div class="kpi"><span class="n">{
        len(passages)
    }</span><span class="l">passages</span></div>
  <div class="kpi good"><span class="n">{
        agreed
    }</span><span class="l">agreed (hidden)</span></div>
  <div class="kpi"><span class="n">{
        len(divergent)
    }</span><span class="l">need judgement</span></div>
  {
        "".join(
            f'<div class="kpi"><span class="n">{len(d["claims"])}</span><span class="l">{e(d["model"])} claims</span></div>'
            for d in digests
        )
    }
</div>
<div class="note"><strong>This is the anti-overload mechanism.</strong> {
        len(digests[0]["claims"]) if digests else 0
    }+ claims per model is unreadable. Agreement is collapsed automatically, so you
  only ever look at the {
        len(divergent)
    } passages where the models actually differ &mdash; and you
  judge those, not the corpus.</div>
<div class="tw"><table>
<thead><tr><th>time</th>{heads}<th>which is better?</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>
</div>
<script>
const verdicts = {{}};
document.querySelectorAll('.judge button').forEach(b => b.addEventListener('click', () => {{
  const p = b.dataset.p;
  document.querySelectorAll(`.judge button[data-p="${{p}}"]`)
    .forEach(x => x.setAttribute('aria-pressed', String(x === b)));
  verdicts[p] = b.dataset.v;
  window.__verdicts = verdicts;
}}));
</script>
</body></html>"""
    out.write_text(doc)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("digests", nargs="+", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--record", default="record")
    args = ap.parse_args()

    ds = [load_digest(p) for p in args.digests]
    ps = align(ds)
    render(ds, ps, args.output, args.record)

    agreed = sum(1 for p in ps if p["verdict"] == "agreed")
    print(
        f"{len(ds)} digests: "
        + ", ".join(f"{d['model']}={len(d['claims'])}" for d in ds)
    )
    print(
        f"{len(ps)} passages | {agreed} agreed (hidden) | {len(ps) - agreed} need judgement"
    )
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
