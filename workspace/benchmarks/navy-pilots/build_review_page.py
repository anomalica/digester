#!/usr/bin/env python3
"""Generate a self-contained HTML review page for navy-pilots digests.

Embeds all digest data inline, embeds YouTube video, model filter chips,
claims-vs-nodes toggle, timestamp-ordered claim cards, and 5-point rating.
Ratings persist in localStorage; export saves them to a JSON file.
"""

from __future__ import annotations
import json
import glob
import uuid
import argparse
import http.server
import socketserver
from pathlib import Path

HERE = Path(__file__).resolve().parent
YAML_DIR = HERE / "model-runs"
OUT = HERE / "review.html"

YOUTUBE_ID = "ZBtMbBPzqHY"

try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML is required: pip install pyyaml")


def load_digests() -> list[dict]:
    models = []
    yaml_files = sorted(glob.glob(str(YAML_DIR / "navy-pilots.*.yaml")))
    for fp in yaml_files:
        path = Path(fp)
        stem = path.stem
        model_id = stem[len("navy-pilots.") :]
        name = model_id.replace("_", " / ", 1).replace("_", " ")

        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
        except Exception as e:
            models.append(
                {
                    "id": model_id,
                    "name": name,
                    "errata": f"parse error: {e}",
                    "claims": [],
                    "nodes": [],
                }
            )
            continue

        if doc is None:
            models.append(
                {
                    "id": model_id,
                    "name": name,
                    "errata": "empty YAML",
                    "claims": [],
                    "nodes": [],
                }
            )
            continue

        claims_raw = doc.get("domain_claims") or []
        nodes_raw = doc.get("nodes") or []

        claims = []
        for c in claims_raw:
            speaker = c.get("speaker", {})
            speaker_name = speaker.get("name") if isinstance(speaker, dict) else None
            claims.append(
                {
                    "id": c.get("id", str(uuid.uuid4())),
                    "type": c.get("type"),
                    "location": c.get("location"),
                    "speaker": speaker_name,
                    "text": c.get("text", ""),
                    "quote": c.get("quote", ""),
                }
            )

        nodes = []
        for n in nodes_raw:
            nodes.append(
                {
                    "id": n.get("id", str(uuid.uuid4())),
                    "type": n.get("type"),
                    "name": n.get("name"),
                }
            )

        models.append(
            {
                "id": model_id,
                "name": name,
                "errata": None if claims else "no claims found",
                "claims": claims,
                "nodes": nodes,
            }
        )

    return models


CSS = r"""\
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0f1117; --surface: #1a1d28; --surface2: #242838;
  --border: #2e3348; --text: #e1e4ed; --text-muted: #8b90a5;
  --accent: #5b8def; --accent-hover: #7aa3ff; --good: #34c759;
  --warn: #ff9f0a; --bad: #ff453a;
  --chip-bg: #242838; --chip-active: #5b8def;
  --radius: 8px; --radius-sm: 4px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
body { background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }
#sidebar { width: 42%; min-width: 380px; background: #000; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
#sidebar iframe { width: 100%; height: 100%; border: 0; }
#main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
#toolbar { padding: 12px 16px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
#model-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; max-height: 160px; overflow-y: auto; }
.chip { padding: 4px 12px; border-radius: 999px; background: var(--chip-bg); border: 1px solid var(--border); cursor: pointer; font-size: 13px; color: var(--text-muted); transition: all .15s; user-select: none; white-space: nowrap; }
.chip:hover { background: #323750; color: var(--text); }
.chip.active { background: var(--chip-active); color: #fff; border-color: var(--chip-active); }
.chip.errata { border-color: var(--warn); }
#toolbar-row2 { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.btn { padding: 4px 14px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--surface); color: var(--text-muted); cursor: pointer; font-size: 13px; font-family: inherit; }
.btn:hover { background: var(--surface2); color: var(--text); }
.btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn.active-sel { background: #2a3f5f; color: var(--accent); border-color: var(--accent); }
#filter-type { display: flex; gap: 4px; }
.filter-count { margin-left: auto; font-size: 12px; color: var(--text-muted); white-space: nowrap; }
#cards { flex: 1; overflow-y: auto; padding: 12px 16px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; margin-bottom: 8px; transition: border-color .15s; }
.card:hover { border-color: #4a5580; }
.card.rated-5 { border-left: 3px solid var(--good); }
.card.rated-4 { border-left: 3px solid #30b350; }
.card.rated-3 { border-left: 3px solid var(--warn); }
.card.rated-2 { border-left: 3px solid #cc7730; }
.card.rated-1 { border-left: 3px solid var(--bad); }
.card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; color: var(--text-muted); flex-wrap: wrap; }
.model-tag { padding: 1px 8px; border-radius: 999px; background: var(--chip-bg); font-size: 11px; border: 1px solid var(--border); white-space: nowrap; }
.claim-type { text-transform: uppercase; font-size: 10px; letter-spacing: .5px; }
.ts-link { font-family: monospace; color: var(--accent); cursor: pointer; }
.ts-link:hover { text-decoration: underline; }
.card-quote { font-style: italic; color: var(--text-muted); font-size: 13px; margin-bottom: 4px; padding-left: 8px; border-left: 2px solid var(--border); line-height: 1.4; }
.card-text { font-size: 14px; line-height: 1.45; margin-bottom: 8px; }
.card-rating { display: flex; gap: 4px; align-items: center; }
.rating-btn { width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--border); background: transparent; color: var(--text-muted); cursor: pointer; font-size: 13px; font-weight: 600; transition: all .12s; font-family: inherit; }
.rating-btn:hover { border-color: var(--accent); color: var(--accent); }
.rating-btn.r5 { background: var(--good); color: #fff; border-color: var(--good); }
.rating-btn.r4 { background: #30b350; color: #fff; border-color: #30b350; }
.rating-btn.r3 { background: var(--warn); color: #fff; border-color: var(--warn); }
.rating-btn.r2 { background: #cc7730; color: #fff; border-color: #cc7730; }
.rating-btn.r1 { background: var(--bad); color: #fff; border-color: var(--bad); }
.rating-label { font-size: 11px; color: var(--text-muted); margin-left: 6px; min-width: 60px; }
#stats-bar { padding: 6px 16px; border-top: 1px solid var(--border); font-size: 12px; color: var(--text-muted); display: flex; gap: 16px; flex-shrink: 0; }
.node-card .node-type { text-transform: uppercase; font-size: 10px; letter-spacing: .5px; }
.no-claims { padding: 40px 20px; text-align: center; color: var(--text-muted); }
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
"""

JS = r"""\
<script>
const LS_KEY = 'navy_pilots_ratings';

const RATING_LABELS = { 5: 'Excellent', 4: 'Good', 3: 'Average', 2: 'Poor', 1: 'Terrible' };

let selectedModels = new Set();
let view = 'claims';
let ratings = {};
let ratedTimer = null;

function loadRatings() {
  try { const s = localStorage.getItem(LS_KEY); if (s) ratings = JSON.parse(s); } catch(e) {}
  fetch('/api/ratings').then(function(r) { if (r.ok) return r.json() }).then(function(server) {
    if (server) { var changed = false; for (var k in server) { if (!ratings[k]) { ratings[k] = server[k]; changed = true } } if (changed) { localStorage.setItem(LS_KEY, JSON.stringify(ratings)); renderCards(); updateStats() } }
  }).catch(function(){})
}
function saveRatings() {
  localStorage.setItem(LS_KEY, JSON.stringify(ratings));
  fetch('/api/ratings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(ratings) }).catch(function(){})
}

function setRating(modelId, claimId, value) {
  const key = modelId + ':' + claimId;
  if (value === null) delete ratings[key];
  else ratings[key] = value;
  saveRatings();
  renderCards();
}

function getRating(modelId, claimId) {
  return ratings[modelId + ':' + claimId] || null;
}

function tsToSecs(ts) {
  if (!ts) return Infinity;
  var p = ts.split('-')[0].split(':');
  if (p.length === 3) return parseInt(p[0])*3600 + parseInt(p[1])*60 + parseFloat(p[2]);
  if (p.length === 2) return parseInt(p[0])*60 + parseFloat(p[1]);
  return Infinity;
}

function fmtTs(ts) { return ts || ''; }

function seekVideo(ts) {
  if (!ts) return;
  var s = Math.floor(tsToSecs(ts));
  if (!isFinite(s)) return;
  var ifr = document.querySelector('#sidebar iframe');
  if (ifr) ifr.src = 'https://www.youtube.com/embed/' + YOUTUBE_ID + '?enablejsapi=1&rel=0&autoplay=1&start=' + s;
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderChips() {
  var c = document.getElementById('model-chips');
  c.innerHTML = MODELS.map(function(m) {
    var cls = selectedModels.has(m.id) ? ' active' : '';
    if (m.errata) cls += ' errata';
    return '<span class="chip' + cls + '" data-model="' + esc(m.id) + '" title="' + esc(m.errata || m.name) + '">' + esc(m.name) + '</span>';
  }).join('');
  c.querySelectorAll('.chip').forEach(function(el) {
    el.addEventListener('click', function() {
      var mid = el.dataset.model;
      if (selectedModels.has(mid)) selectedModels.delete(mid);
      else selectedModels.add(mid);
      renderChips();
      renderCards();
    });
  });
  updateStats();
}

function renderCards() {
  var container = document.getElementById('cards');
  var countEl = document.getElementById('filter-count');
  var sel = MODELS.filter(function(m) { return selectedModels.has(m.id); });

  if (sel.length === 0) {
    container.innerHTML = '<div class="no-claims">Select one or more models above to see their digests</div>';
    countEl.textContent = '0 items';
    return;
  }

  if (view === 'claims') {
    var claims = [];
    sel.forEach(function(m) {
      m.claims.forEach(function(c) {
        claims.push({ id: c.id, type: c.type, location: c.location, speaker: c.speaker,
                      text: c.text, quote: c.quote, modelId: m.id, modelName: m.name });
      });
    });
    claims.sort(function(a, b) { return tsToSecs(a.location) - tsToSecs(b.location); });

    if (claims.length === 0) {
      container.innerHTML = '<div class="no-claims">No claims in selected models</div>';
      countEl.textContent = '0 claims';
      return;
    }
    countEl.textContent = claims.length + ' claims';

    container.innerHTML = claims.map(function(c) {
      var r = getRating(c.modelId, c.id);
      var rc = r ? ' rated-' + r : '';
      var btns = [5,4,3,2,1].map(function(v) {
        return '<button class="rating-btn' + (r === v ? ' r' + v : '') + '" data-rating="' + v + '">' + v + '</button>';
      }).join('');
      var label = r ? RATING_LABELS[r] : 'Not rated';
      return '<div class="card' + rc + '" data-model="' + esc(c.modelId) + '" data-claim="' + esc(c.id) + '">'
        + '<div class="card-header">'
        + '<span class="model-tag">' + esc(c.modelName) + '</span>'
        + '<span class="claim-type">' + esc(c.type || '') + '</span>'
        + (c.location ? '<span class="ts-link" data-ts="' + esc(c.location) + '">' + fmtTs(c.location) + '</span>' : '')
        + (c.speaker ? '<span>' + esc(' — ' + c.speaker) + '</span>' : '')
        + '</div>'
        + '<div class="card-quote">' + esc(c.quote) + '</div>'
        + '<div class="card-text">' + esc(c.text) + '</div>'
        + '<div class="card-rating">' + btns + '<span class="rating-label">' + label + '</span></div>'
        + '</div>';
    }).join('');

    container.querySelectorAll('.rating-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var card = btn.closest('.card');
        var v = parseInt(btn.dataset.rating);
        var cur = getRating(card.dataset.model, card.dataset.claim);
        setRating(card.dataset.model, card.dataset.claim, cur === v ? null : v);
      });
    });
    container.querySelectorAll('.ts-link').forEach(function(el) {
      el.addEventListener('click', function() { seekVideo(el.dataset.ts); });
    });

  } else {
    var nodes = [];
    sel.forEach(function(m) {
      m.nodes.forEach(function(n) {
        nodes.push({ id: n.id, type: n.type, name: n.name, modelId: m.id, modelName: m.name });
      });
    });
    if (nodes.length === 0) {
      container.innerHTML = '<div class="no-claims">No nodes in selected models</div>';
      countEl.textContent = '0 nodes';
      return;
    }
    var grouped = {};
    nodes.forEach(function(n) {
      var k = n.type + '::' + n.name;
      if (!grouped[k]) grouped[k] = { type: n.type, name: n.name, models: [] };
      grouped[k].models.push(n.modelName);
    });
    var sorted = Object.values(grouped).sort(function(a, b) {
      if (a.type !== b.type) return a.type.localeCompare(b.type);
      return a.name.localeCompare(b.name);
    });
    countEl.textContent = sorted.length + ' unique nodes';
    container.innerHTML = sorted.map(function(g) {
      var tags = [];
      var seen = {};
      g.models.forEach(function(m) { if (!seen[m]) { seen[m] = true; tags.push('<span class="model-tag">' + esc(m) + '</span>'); } });
      return '<div class="card node-card"><div class="card-header"><span class="node-type">' + esc(g.type) + '</span> ' + tags.join(' ') + '</div><div class="card-text">' + esc(g.name) + '</div></div>';
    }).join('');
  }
}

function updateStats() {
  var total = 0, rated = 0;
  MODELS.forEach(function(m) {
    m.claims.forEach(function(c) { total++; if (getRating(m.id, c.id)) rated++; });
  });
  document.getElementById('stat-rated').textContent = rated + ' rated';
  document.getElementById('stat-total').textContent = total + ' total claims';
  document.getElementById('stat-models').textContent = MODELS.length + ' models';
}

function selectAll() {
  MODELS.forEach(function(m) { selectedModels.add(m.id); });
  renderChips(); renderCards();
}

function selectErrata() {
  MODELS.forEach(function(m) { if (m.errata) selectedModels.add(m.id); });
  renderChips(); renderCards();
}

function exportRatings() {
  var blob = new Blob([JSON.stringify(ratings, null, 2)], {type: 'application/json'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'ratings-export-' + new Date().toISOString().slice(0,10) + '.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

function importRatingsClick() {
  document.getElementById('import-file').click();
}

function importRatings(file) {
  var reader = new FileReader();
  reader.onload = function() {
    try {
      var data = JSON.parse(reader.result);
      var merged = 0;
      for (var k in data) {
        if (!ratings[k]) { ratings[k] = data[k]; merged++; }
      }
      if (merged > 0) {
        saveRatings();
        renderCards();
        updateStats();
        alert('Imported ' + merged + ' new ratings.');
      } else {
        alert('No new ratings to import (all keys already known).');
      }
    } catch(e) { alert('Invalid ratings file: ' + e.message); }
  };
  reader.readAsText(file);
}

document.addEventListener('DOMContentLoaded', function() {
  loadRatings();
  if (MODELS.length > 0) selectedModels.add(MODELS[0].id);
  renderChips(); renderCards(); updateStats();

  document.querySelectorAll('#filter-type .btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('#filter-type .btn').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      view = btn.dataset.view;
      renderCards();
    });
  });

  document.getElementById('btn-clear').addEventListener('click', function() {
    selectedModels.clear(); renderChips(); renderCards();
  });
  document.getElementById('btn-all').addEventListener('click', selectAll);
  document.getElementById('btn-err').addEventListener('click', selectErrata);
  document.getElementById('btn-export').addEventListener('click', exportRatings);
  document.getElementById('btn-import').addEventListener('click', importRatingsClick);
  document.getElementById('import-file').addEventListener('change', function() {
    if (this.files.length) importRatings(this.files[0]);
  });
});
</script>
"""

HTML = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Digest Review — Navy Pilots</title>
<style>{CSS}</style>
</head>
<body>
<div id="sidebar">
  <iframe src="https://www.youtube.com/embed/{YOUTUBE_ID}?enablejsapi=1&rel=0"
          allow="autoplay; fullscreen" allowfullscreen></iframe>
</div>
<div id="main">
  <div id="toolbar">
    <div id="model-chips"></div>
    <div id="toolbar-row2">
      <div id="filter-type">
        <button class="btn active" data-view="claims">Claims</button>
        <button class="btn" data-view="nodes">Nodes</button>
      </div>
      <button class="btn" id="btn-clear">Clear all</button>
      <button class="btn" id="btn-all">Select all</button>
      <button class="btn" id="btn-err">Select bad</button>
      <button class="btn" id="btn-export">Export ratings</button>
      <button class="btn" id="btn-import">Import ratings</button>
      <input type="file" id="import-file" accept=".json" style="display:none">
      <span class="filter-count" id="filter-count"></span>
    </div>
  </div>
  <div id="cards"></div>
  <div id="stats-bar">
    <span id="stat-rated">0 rated</span>
    <span id="stat-total">0 total claims</span>
    <span id="stat-models">0 models</span>
  </div>
</div>
<script>
var MODELS = __DATA__;
var YOUTUBE_ID = __YOUTUBE_ID_JS__;
</script>
{JS}
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate (and optionally serve) the navy-pilots review page"
    )
    ap.add_argument(
        "--serve", action="store_true", help="Start HTTP server after generating"
    )
    ap.add_argument(
        "--port", type=int, default=8899, help="Port for the server (default: 8899)"
    )
    args = ap.parse_args()

    print(f"Loading digests from {YAML_DIR}...")
    models = load_digests()
    data_json = json.dumps(models, ensure_ascii=False, separators=(",", ":"))
    yt_json = json.dumps(YOUTUBE_ID)

    page = HTML.replace("__DATA__", data_json).replace("__YOUTUBE_ID_JS__", yt_json)
    OUT.write_text(page, encoding="utf-8")

    total_claims = sum(len(m["claims"]) for m in models)
    total_nodes = sum(len(m["nodes"]) for m in models)
    print(f"  {len(models)} models, {total_claims} claims, {total_nodes} nodes")
    print(f"  written to {OUT} ({OUT.stat().st_size / 1024 / 1024:.1f} MB)")

    if args.serve:
        serve(args.port)
    else:
        print(f"\nOpen file://{OUT} (or run with --serve for HTTP)")
    return 0


def serve(port: int):
    ratings_path = HERE / "ratings.json"
    generated_page = OUT.read_text(encoding="utf-8")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(generated_page.encode("utf-8"))
            elif self.path == "/api/ratings":
                data = b"{}"
                if ratings_path.exists():
                    data = ratings_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/api/ratings":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length > 0 else b"{}"
                ratings_path.write_bytes(body)
                resp = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            print(f"  {args[0]}")

    server = socketserver.ThreadingTCPServer(("0.0.0.0", port), Handler)
    server.allow_reuse_address = True
    print(f"\nServing on http://localhost:{port}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
