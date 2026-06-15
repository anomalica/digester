#!/usr/bin/env python3
"""Full interactive explorer: every claim from every iteration, both models,
with entity-type chips, grouped by source moment, iterations you toggle."""

import html
import json
import re
from pathlib import Path

import yaml

EXP = Path("/tmp/digester-exp")
SRC = Path(
    "/home/mark/repos/anomalica/ingests/store/"
    "1405206f070621abea9b5131b1512eebf13896ce9dc0ced476f4159c796c7e44.md"
)
OLD = "2021-05-17-video-navy-pilots-describe-encounters-with-ufos"

ITERS = {
    "haiku": [
        ("baseline", f"{OLD}.haiku.yaml"),
        ("1: attestation+Q&A", "navy.haiku.new.yaml"),
        ("2: units/durability/British", "navy.haiku.v2.yaml"),
        ("3: orientation-grounded", "navy.haiku.r1.yaml"),
        ("4: assertion+node sweep", "navy.haiku.r2.yaml"),
        ("5: haiku hard rules (FINAL)", "navy.haiku.r3.yaml"),
    ],
    "sonnet": [
        ("baseline", f"{OLD}.sonnet.yaml"),
        ("1: attestation+Q&A", "navy.sonnet.new.yaml"),
        ("2: units/durability/British", "navy.sonnet.v2.yaml"),
        ("3: orientation-grounded", "navy.sonnet.r1.yaml"),
        ("4: assertion+node sweep (FINAL)", "navy.sonnet.r2.yaml"),
    ],
}


def ts_to_sec(s):
    if not s:
        return None
    s = re.split(r"[-–—]", str(s))[0].strip()
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?$", s)
    return (
        None
        if not m
        else int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    )


def fmt(sec):
    if sec is None:
        return "?"
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_transcript(text):
    body = text.split("---", 2)[-1]
    segs, spk = [], None
    for line in body.splitlines():
        sm = re.match(r"<!--\s*speaker:\s*(.+?)\s*-->", line)
        if sm:
            spk = sm.group(1)
            continue
        lm = re.match(r"^(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\s+(.*)$", line.strip())
        if lm:
            segs.append(
                {"sec": ts_to_sec(lm.group(1)), "spk": spk, "text": lm.group(2).strip()}
            )
    return segs


def bk(sec):
    return -1 if sec is None else (sec // 4) * 4


def load(fname):
    d = yaml.safe_load(open(EXP / fname))
    ntype = {}
    nodes_by_type = {}
    for n in d.get("nodes", []) or []:
        t = n.get("type", "?")
        nm = n.get("name", "")
        ntype[nm] = t
        nodes_by_type.setdefault(t, []).append(nm)
    claims = []
    for c in d.get("domain_claims", []) or []:
        sp = c.get("speaker")
        sp = sp.get("name") if isinstance(sp, dict) else sp
        refs = []
        for r in c.get("refs") or []:
            nm = r.get("name") if isinstance(r, dict) else r
            refs.append({"name": nm, "type": ntype.get(nm, "?")})
        claims.append(
            {
                "loc": c.get("location", ""),
                "bk": bk(ts_to_sec(c.get("location", ""))),
                "text": c.get("text", "") or c.get("content", ""),
                "att": c.get("attestation") or "",
                "type": c.get("type", ""),
                "cat": c.get("category", "domain"),
                "spk": sp or "",
                "quote": c.get("quote", ""),
                "refs": refs,
            }
        )
    return {
        "claims": claims,
        "nodes_by_type": nodes_by_type,
        "n_claims": len(claims),
        "n_nodes": sum(len(v) for v in nodes_by_type.values()),
    }


transcript = parse_transcript(SRC.read_text())
data = {}
for model, iters in ITERS.items():
    data[model] = []
    for label, fname in iters:
        try:
            d = load(fname)
            d["label"] = label
            data[model].append(d)
        except FileNotFoundError:
            pass

# source line per bucket
src_by_bucket = {}
for s in transcript:
    src_by_bucket.setdefault(bk(s["sec"]), []).append(s)


def esc(s):
    return html.escape(str(s) if s is not None else "")


P = []
P.append("""<!doctype html><html><head><meta charset="utf-8"><title>Digester extraction explorer</title>
<style>
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1a1a1a;background:#f4f4f6;}
header{background:#15152a;color:#fff;padding:12px 18px;} header h1{margin:0;font-size:18px;}
header p{margin:4px 0 0;color:#bcc;font-size:12.5px;}
.bar{position:sticky;top:0;z-index:30;background:#23233f;color:#fff;padding:8px 18px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px;box-shadow:0 2px 6px rgba(0,0,0,.25);}
.bar b{margin-right:4px;} .bar label{cursor:pointer;margin-right:6px;white-space:nowrap;}
.bar .grp{display:flex;gap:4px;align-items:center;flex-wrap:wrap;}
.bar button{font:13px inherit;background:#4a4a7a;color:#fff;border:none;border-radius:5px;padding:4px 10px;cursor:pointer;}
.bar button.on{background:#2e8b57;font-weight:700;}
.wrap{max-width:1500px;margin:0 auto;padding:12px 14px 80px;}
.moment{margin-top:12px;border:1px solid #e2e2e6;border-radius:7px;overflow:hidden;background:#fff;}
.src{background:#fffbe9;border-bottom:1px solid #e8d98a;padding:7px 11px;font-size:12.5px;}
.src .spk{font-weight:700;color:#7a6500;}
.cols{display:flex;align-items:stretch;}
.col{flex:1 1 0;width:0;border-right:1px solid #ededf0;padding:7px 9px;min-width:0;}
.col:last-child{border-right:none;}
.col h5{margin:0 0 5px;font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;color:#777;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.claim{border:1px solid #e6e6ea;border-radius:6px;padding:6px 7px;margin-bottom:6px;background:#fcfcfd;font-size:12.5px;}
.claim .t{margin-bottom:4px;}
.chips{display:flex;flex-wrap:wrap;gap:3px;}
.chip{font-size:10px;font-weight:600;border-radius:9px;padding:1px 7px;white-space:nowrap;}
.att-first{background:#fde9d4;color:#8a4b12;} .att-second{background:#d9e8fb;color:#15487e;} .att-third{background:#eadcf7;color:#5b2b8a;} .att-none{background:#eee;color:#999;}
.ctype{background:#ececf2;color:#555;}
.spk{background:#fff0f5;color:#a3306a;}
.r-person{background:#dfeaff;color:#1c4e8a;} .r-place{background:#e2f6e6;color:#1f6f4a;} .r-event{background:#ffe9d6;color:#9a531a;}
.r-organisation{background:#efe2fb;color:#6a3d9a;} .r-project{background:#fde6f0;color:#9a2d6a;} .r-object{background:#e4f3f7;color:#1a6b7e;}
.r-topic{background:#f5efd9;color:#7a6500;} .r-document{background:#eceff1;color:#445;} .r-x{background:#eee;color:#888;}
.q{color:#888;font-style:italic;font-size:10.5px;margin-top:3px;}
.empty{color:#ccc;font-size:11px;}
mark.imp{background:#ffd24d;border-radius:2px;}
.nodepanel{display:flex;gap:14px;flex-wrap:wrap;}
.nodecol{flex:1 1 230px;border:1px solid #e2e2e6;border-radius:7px;padding:10px 12px;background:#fff;}
.nodecol h4{margin:0 0 8px;font-size:13px;}
.ntype{font-size:11px;font-weight:700;color:#666;margin:7px 0 2px;text-transform:uppercase;}
.hidden{display:none;}
.count{background:#15152a;padding:2px 8px;border-radius:10px;font-size:12px;}
</style></head><body>""")

P.append("""<header><h1>Digester extraction explorer - every iteration, every claim</h1>
<p>One document, both models, all tuning cycles. Pick a model and which iterations to show; scroll the whole transcript to see every extracted claim with its entity tags. Toggle Claims / Nodes.</p></header>""")

P.append("""<div class="bar">
<span class="grp"><b>model:</b>
<label><input type="radio" name="model" value="haiku" checked onchange="setModel('haiku')">haiku</label>
<label><input type="radio" name="model" value="sonnet" onchange="setModel('sonnet')">sonnet</label></span>
<span class="grp"><b>iterations:</b><span id="iterboxes"></span></span>
<span class="grp"><button id="vClaims" class="on" onclick="setView('claims')">Claims</button>
<button id="vNodes" onclick="setView('nodes')">Nodes</button></span>
<span class="count" id="summary"></span>
</div><div class="wrap"><div id="claimsView"></div><div id="nodesView" class="hidden"></div></div>""")

P.append("<script>")
P.append("const DATA=" + json.dumps(data) + ";")
P.append("const SRC=" + json.dumps(src_by_bucket) + ";")
P.append("""
let model='haiku';
let sel=new Set();
function finalIdx(){return DATA[model].length-1;}
function defaultSel(){return new Set([0, finalIdx()]);}
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function highlightImp(t){return esc(t).replace(/(\\d[\\d,\\.]*\\s*(?:miles per hour|mph|miles?|feet|foot|knots?))/ig,'<mark class="imp">$1</mark>');}
function setModel(m){model=m;sel=defaultSel();renderIterBoxes();render();}
function setView(v){
  document.getElementById('claimsView').classList.toggle('hidden',v!=='claims');
  document.getElementById('nodesView').classList.toggle('hidden',v!=='nodes');
  document.getElementById('vClaims').classList.toggle('on',v==='claims');
  document.getElementById('vNodes').classList.toggle('on',v==='nodes');
}
function renderIterBoxes(){
  const box=document.getElementById('iterboxes');
  box.innerHTML=DATA[model].map((it,i)=>
    `<label><input type="checkbox" ${sel.has(i)?'checked':''} onchange="toggleIter(${i})">${esc(it.label)}</label>`).join(' ');
}
function toggleIter(i){ if(sel.has(i))sel.delete(i); else sel.add(i); render(); }
function attClass(a){return a?('att-'+a.split('_')[0]):'att-none';}
function chip(cls,txt){return `<span class="chip ${cls}">${esc(txt)}</span>`;}
function renderClaim(c){
  let chips=`<span class="chip ${attClass(c.att)}">${esc(c.att||'no attest')}</span>`;
  chips+=chip('ctype',c.type);
  if(c.cat==='infrastructure')chips+=chip('ctype','infra');
  if(c.spk)chips+=chip('spk','🗣 '+c.spk);
  for(const r of c.refs)chips+=chip('r-'+(r.type||'x'),r.name);
  let q=c.quote?`<div class="q">&ldquo;${esc(c.quote.slice(0,180))}&rdquo;</div>`:'';
  return `<div class="claim"><div class="t">${highlightImp(c.text)}</div><div class="chips">${chips}</div>${q}</div>`;
}
function render(){
  const iters=[...sel].sort((a,b)=>a-b);
  // summary
  const tot=iters.map(i=>DATA[model][i]).reduce((a,it)=>a+it.n_claims,0);
  document.getElementById('summary').textContent=
    `${model}: ${DATA[model].length} iterations, showing ${iters.length} (${iters.map(i=>DATA[model][i].n_claims).join(' / ')} claims)`;
  // claims by bucket
  const buckets=new Set();
  for(const i of iters)for(const c of DATA[model][i].claims)buckets.add(c.bk);
  const order=[...buckets].filter(b=>b>=0).sort((a,b)=>a-b);
  if([...buckets].includes(-1))order.push(-1);
  let html='';
  for(const bktick of order){
    const srcs=SRC[bktick]||[];
    let srcline = srcs.length
      ? srcs.map(s=>`<div class="src"><span class="spk">[${s.sec!=null?fmtSec(s.sec):'?'}] ${esc(s.spk||'')}:</span> ${esc(s.text)}</div>`).join('')
      : `<div class="src"><span class="spk">${bktick<0?'(no timestamp)':'['+fmtSec(bktick)+']'}</span></div>`;
    let cols='';
    for(const i of iters){
      const cl=DATA[model][i].claims.filter(c=>c.bk===bktick);
      const inner = cl.length? cl.map(renderClaim).join('') : '<span class="empty">&mdash;</span>';
      cols+=`<div class="col"><h5>${esc(DATA[model][i].label)}</h5>${inner}</div>`;
    }
    html+=`<div class="moment">${srcline}<div class="cols">${cols}</div></div>`;
  }
  document.getElementById('claimsView').innerHTML=html||'<p>Select at least one iteration.</p>';
  // nodes
  const TYPES=['person','place','event','organisation','project','object','topic','document'];
  let nh='<div class="nodepanel">';
  for(const i of iters){
    const it=DATA[model][i];
    nh+=`<div class="nodecol"><h4>${esc(it.label)} <span style="color:#999;font-weight:400">(${it.n_nodes} nodes)</span></h4>`;
    for(const t of TYPES){
      const ns=it.nodes_by_type[t]||[];
      if(!ns.length)continue;
      nh+=`<div class="ntype">${t} (${ns.length})</div><div class="chips">`+
        ns.map(n=>`<span class="chip r-${t}">${esc(n)}</span>`).join('')+`</div>`;
    }
    nh+='</div>';
  }
  nh+='</div>';
  document.getElementById('nodesView').innerHTML=nh;
}
function fmtSec(sec){const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=sec%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`;}
sel=defaultSel();renderIterBoxes();render();
""")
P.append("</script></body></html>")

out = EXP / "explorer.html"
out.write_text("".join(P))
print("wrote", out, out.stat().st_size, "bytes")
for m in ITERS:
    print(
        f"  {m}: {len(data[m])} iterations:",
        ", ".join(f"{d['label']}({d['n_claims']}c/{d['n_nodes']}n)" for d in data[m]),
    )
