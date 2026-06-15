#!/usr/bin/env python3
"""Build a single self-contained page showing the prompt-engineering progression."""

import html
import json
import re
from pathlib import Path

import yaml

EXP = Path("/tmp/digester-exp")
OLD = "2021-05-17-video-navy-pilots-describe-encounters-with-ufos"
prog = json.load(open(EXP / "progression.json"))

# claim recall measured (LLM-judged, approximate) for baseline/final
CLAIM_RECALL = {
    ("haiku", "baseline"): 0.90,
    ("haiku", "round 3 (haiku hard rules) FINAL"): 0.98,
    ("sonnet", "baseline"): 1.00,
    ("sonnet", "round 2 (assertion + node sweep) FINAL"): 1.00,
}

CHANGELOG = {
    "baseline": "Original production prompt (pre-tuning). Attestation forced (narration stamped first_hand); reported-speech style; imperial sometimes left in.",
    "attempt 1 (units/Q&A/durability)": "First fixes: SI units, optional attestation, opinion/Q&A capture, durability, British English. REGRESSED: the 'expand into a standalone assertion' wording accidentally bred 'X stated that ...' anchors (23!), and atomicity slipped. This is the dip you caught.",
    "round 1 (orientation-grounded)": "Re-grounded in the architecture docs/ADRs. Hard atomicity (split capability lists), SI full unit names (no imperial even in parens), Q&A quote carries both turns, location as timestamp range. Very exhaustive.",
    "round 2 (assertion + node sweep)": "Forceful ASSERTION-not-reported-speech section with concrete rewrites (killed all anchors), plus a node COMPLETENESS sweep (topics, military/legislative orgs, aircraft). Sonnet reaches node recall 1.0 here.",
    "round 2 (assertion + node sweep) FINAL": "Forceful ASSERTION-not-reported-speech rewrites (anchors -> 0) and node COMPLETENESS sweep. Sonnet's final: node recall 1.0, claim recall 1.0, zero quality defects.",
    "round 3 (haiku hard rules) FINAL": "Haiku-only divergence: a hard 'never bundle 2+ numbers / 3+ properties' atomicity rule and an explicit 'emit every named entity' node sweep. Cleaned compound 7->1 and lifted node recall 0.83->0.90.",
}

EXEMPLARS = [
    {
        "key": "cap",
        "title": "The capability list (atomicity)",
        "src": 'Luis Elizondo: "Imagine a technology that can do 600 to 700 G-forces, that can fly at 13,000 miles an hour, that can evade radar, and that can fly through air and water and possibly space, and ... has no obvious signs of propulsion, no wings, no control surfaces, and yet still can defy the natural effects of Earth\'s gravity."',
        "prefix": "00:02:12",
        "note": "Should split into ~6 atomic claims, each unit in SI.",
    },
    {
        "key": "rc",
        "title": "Russian/Chinese - a question-and-answer (Q&A + no reported-speech)",
        "src": 'Bill Whitaker: "Could it be Russian or Chinese technology?"  Ryan Graves: "I don\'t see why not."',
        "prefix": "00:06:0",
        "note": "Claim = the answer (Graves), no 'stated that', quote should carry BOTH turns.",
    },
    {
        "key": "radar",
        "title": "Princeton radar (units)",
        "src": 'Bill Whitaker: "...descending 80,000 feet in less than a second."',
        "prefix": "00:07:23",
        "note": "Imperial in source -> SI in the claim; original kept in the quote.",
    },
]

STAGE_FILES = {
    ("haiku", "baseline"): f"{OLD}.haiku.yaml",
    ("haiku", "FINAL"): "navy.haiku.r3.yaml",
    ("sonnet", "baseline"): f"{OLD}.sonnet.yaml",
    ("sonnet", "FINAL"): "navy.sonnet.r2.yaml",
}


def load_claims(fname):
    d = yaml.safe_load(open(EXP / fname))
    return d.get("domain_claims", []) or []


def claims_at(claims, prefix):
    out = []
    for c in claims:
        if str(c.get("location", "")).startswith(prefix):
            out.append(c)
    return out


def esc(s):
    return html.escape(str(s) if s is not None else "")


P = []
P.append("""<!doctype html><html><head><meta charset="utf-8"><title>Digester prompt-engineering progression</title>
<style>
body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1a1a1a;background:#f4f4f6;}
header{background:#15152a;color:#fff;padding:16px 22px;}
header h1{margin:0 0 4px;font-size:20px;} header p{margin:0;color:#bcc;font-size:13px;}
.wrap{max-width:1280px;margin:0 auto;padding:14px 16px 60px;}
section{background:#fff;margin:16px 0;border:1px solid #e2e2e6;border-radius:8px;padding:16px 20px;}
section h2{margin:0 0 10px;font-size:17px;border-bottom:2px solid #eee;padding-bottom:6px;}
.note{font-size:12.5px;color:#666;}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px;}
th,td{border:1px solid #e3e3e7;padding:6px 9px;text-align:left;vertical-align:top;}
th{background:#f0f0f3;}
.mh{color:#b5651d;font-weight:700;} .ms{color:#1f6f4a;font-weight:700;}
td.num{text-align:center;font-weight:600;}
.dip{background:#fff3f0;}
.final{background:#eafaf0;}
.big{display:flex;gap:18px;flex-wrap:wrap;}
.card{flex:1 1 280px;border:1px solid #e2e2e6;border-radius:8px;padding:12px 14px;}
.card h3{margin:0 0 8px;font-size:15px;}
.card .row{display:flex;justify-content:space-between;font-size:13px;padding:2px 0;border-bottom:1px solid #f2f2f4;}
.b4{color:#999;} .af{font-weight:700;}
.good{color:#1f6f4a;} .bad{color:#c0392b;} .mid{color:#b5651d;}
.ex{border:1px solid #e6e6ea;border-radius:7px;margin:10px 0;overflow:hidden;}
.ex .src{background:#fffbe9;border-bottom:1px solid #e8d98a;padding:8px 11px;font-size:13px;}
.ex .src b{color:#7a6500;}
.exgrid{display:grid;grid-template-columns:1fr 1fr;gap:0;}
.exgrid>div{border-right:1px solid #ededf0;padding:8px 11px;}
.exgrid>div:nth-child(2n){border-right:none;}
.exgrid h4{margin:0 0 5px;font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;color:#555;}
.cl{font-size:12.5px;border-left:3px solid #ddd;padding:3px 0 3px 8px;margin-bottom:5px;}
.cl.b{border-color:#d9b38c;background:#fcf7f1;} .cl.f{border-color:#9ad6b0;background:#f1faf4;}
.q{color:#777;font-style:italic;font-size:11px;}
mark.imp{background:#ffd24d;}
.cnt{font-size:11px;color:#888;}
</style></head><body>""")

P.append("""<header><h1>Digester extraction - prompt-engineering progression</h1>
<p>One document (the 2021 60 Minutes Navy-pilots segment), two models, tuned separately over several cycles.
Graded against a hand-built ground truth. Lower defect counts and higher recall are better.</p></header><div class="wrap">""")

# headline before/after
P.append('<section><h2>Headline: baseline &rarr; final</h2><div class="big">')
for model, mc in (("haiku", "mh"), ("sonnet", "ms")):
    rows = prog[model]
    base = rows[0]
    fin = rows[-1]
    cr_b = CLAIM_RECALL.get((model, base["stage"]))
    cr_f = CLAIM_RECALL.get((model, fin["stage"]))

    def line(lbl, b, f, better_low=True):
        try:
            bn, fn = float(b), float(f)
            cls = (
                "good"
                if ((fn <= bn) == better_low) and fn != bn
                else ("b4" if fn == bn else "bad")
            )
        except (TypeError, ValueError):
            cls = ""
        return (
            f'<div class="row"><span>{lbl}</span><span>'
            f'<span class="b4">{b}</span> &rarr; <span class="af {cls}">{f}</span></span></div>'
        )

    P.append(f'<div class="card"><h3 class="{mc}">{model}</h3>')
    P.append(
        line("node recall", base["node_recall"], fin["node_recall"], better_low=False)
    )
    P.append(
        line(
            "claim recall*",
            cr_b if cr_b is not None else "-",
            cr_f if cr_f is not None else "-",
            better_low=False,
        )
    )
    P.append(line("claims extracted", base["claims"], fin["claims"], better_low=False))
    P.append(line("imperial units", base["imperial"], fin["imperial"]))
    P.append(
        line("reporting-anchors", base["reporting_anchor"], fin["reporting_anchor"])
    )
    P.append(line("merged claims", base["compound"], fin["compound"]))
    P.append(line("non-durable refs", base["vague"], fin["vague"]))
    P.append(line("American spelling", base["american"], fin["american"]))
    P.append("</div>")
P.append(
    '</div><p class="note">*claim recall is LLM-judged (approximate) - fraction of must-capture facts present. Sonnet always captured the facts; its baseline problems were quality, not coverage.</p></section>'
)

# trajectory table
P.append("<section><h2>Cycle-by-cycle trajectory</h2>")
for model, mc in (("haiku", "mh"), ("sonnet", "ms")):
    P.append(
        f'<h3 class="{mc}">{model}</h3><table><tr>'
        "<th>cycle</th><th>claims</th><th>node recall</th><th>imperial</th>"
        "<th>reporting-anchor</th><th>merged</th><th>vague</th><th>amEng</th><th>what changed</th></tr>"
    )
    for i, r in enumerate(prog[model]):
        rowcls = (
            "dip"
            if "attempt 1" in r["stage"]
            else ("final" if "FINAL" in r["stage"] else "")
        )
        P.append(
            f'<tr class="{rowcls}"><td>{esc(r["stage"])}</td>'
            f'<td class="num">{r["claims"]}</td><td class="num">{r["node_recall"]}</td>'
            f'<td class="num">{r["imperial"]}</td><td class="num">{r["reporting_anchor"]}</td>'
            f'<td class="num">{r["compound"]}</td><td class="num">{r["vague"]}</td><td class="num">{r["american"]}</td>'
            f'<td class="note">{esc(CHANGELOG.get(r["stage"], ""))}</td></tr>'
        )
    P.append("</table>")
P.append(
    '<p class="note">Pink = the regression you caught (my first attempt bred "X stated that" anchors). Green = the chosen final per model.</p></section>'
)

# exemplar evolution
P.append(
    "<section><h2>See it on real claims: baseline &rarr; final</h2>"
    '<p class="note">The same source moment, as each model rendered it before tuning and after. This is the inspectable proof.</p>'
)

base_h = load_claims(STAGE_FILES[("haiku", "baseline")])
fin_h = load_claims(STAGE_FILES[("haiku", "FINAL")])
base_s = load_claims(STAGE_FILES[("sonnet", "baseline")])
fin_s = load_claims(STAGE_FILES[("sonnet", "FINAL")])


def render_cl(claims, cls):
    if not claims:
        return (
            '<div class="cl '
            + cls
            + '"><span class="q">(nothing extracted here)</span></div>'
        )
    out = []
    for c in claims:
        txt = esc(c.get("text", ""))
        txt = re.sub(
            r"(\d[\d,\.]*\s*(?:miles per hour|mph|miles?|feet|foot|knots?))",
            r'<mark class="imp">\1</mark>',
            txt,
            flags=re.I,
        )
        q = c.get("quote", "")
        qline = f'<div class="q">&ldquo;{esc(q[:200])}&rdquo;</div>' if q else ""
        att = c.get("attestation") or "-"
        sp = c.get("speaker", {})
        sp = sp.get("name") if isinstance(sp, dict) else (sp or "-")
        out.append(
            f'<div class="cl {cls}">{txt}<div class="cnt">[{esc(att)} &middot; {esc(sp)}]</div>{qline}</div>'
        )
    return "".join(out)


for ex in EXEMPLARS:
    pfx = ex["prefix"]
    P.append('<div class="ex">')
    P.append(
        f'<div class="src"><b>{esc(ex["title"])}</b><br>{esc(ex["src"])}<br>'
        f'<span class="note">target: {esc(ex["note"])}</span></div>'
    )
    for model, b, f in (("haiku", base_h, fin_h), ("sonnet", base_s, fin_s)):
        bc = claims_at(b, pfx)
        fc = claims_at(f, pfx)
        P.append(
            f'<div style="padding:6px 11px;font-size:12px;font-weight:700" class="{"mh" if model == "haiku" else "ms"}">{model}</div>'
        )
        P.append('<div class="exgrid">')
        P.append(
            f"<div><h4>baseline ({len(bc)} claim{'s' if len(bc) != 1 else ''})</h4>{render_cl(bc, 'b')}</div>"
        )
        P.append(
            f"<div><h4>final ({len(fc)} claim{'s' if len(fc) != 1 else ''})</h4>{render_cl(fc, 'f')}</div>"
        )
        P.append("</div>")
    P.append("</div>")
P.append("</section>")

P.append(
    '<section><h2>The rules that got tuned in</h2><ul class="note" style="font-size:13px;line-height:1.7">'
    "<li><b>Atomicity</b>: one fact per claim; a capability list becomes ~6 claims.</li>"
    "<li><b>Units</b>: SI with full names in the claim; original units only in the quote (never imperial in parens).</li>"
    '<li><b>Assertion not reported speech</b>: never "X stated that ..."; name the actor as subject or drop the name (it is in the speaker field).</li>'
    "<li><b>Q&amp;A</b>: claim = the answer; the quote carries both the question and the answer.</li>"
    '<li><b>Durability</b>: resolve "the video footage"/"these objects" so each claim stands alone.</li>'
    "<li><b>Attestation optional</b>; British English; node completeness (topics, military/legislative orgs, aircraft); timestamp-range locations.</li>"
    "</ul></section>"
)

P.append("</div></body></html>")
out = EXP / "progression.html"
out.write_text("".join(P))
print("wrote", out, out.stat().st_size, "bytes")
