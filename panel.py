#!/usr/bin/env python3
"""Local control panel. Serves one page that polls .orch-state.json.

    python3 panel.py            # http://127.0.0.1:8765
"""
import http.server, json, pathlib, sys

ROOT = pathlib.Path(__file__).parent
FILES = {"/state": ROOT / ".orch-state.json", "/stats": ROOT / ".orch-stats.json"}
STOP = ROOT / ".orch-stop"
RUNS = ROOT / ".orch-runs"

PAGE = """<!doctype html><meta charset=utf-8><title>orch</title>
<style>
body{font:14px/1.4 system-ui,sans-serif;margin:1.5rem;background:#111;color:#ddd}
h1{font-size:1.1rem;margin:0 0 1rem}.bar{display:flex;gap:1.5rem;align-items:center;margin-bottom:1rem}
button{background:#822;color:#fff;border:0;padding:.4rem .8rem;border-radius:4px;cursor:pointer}
table{border-collapse:collapse;width:100%;margin-bottom:1rem}td,th{text-align:left;padding:.35rem .6rem;border-bottom:1px solid #333;vertical-align:top}
.pending{color:#777}.running{color:#fc3}.passed,.merged{color:#6d6}.escalating{color:#f93}.failed{color:#f55}
pre{background:#000;padding:.6rem;max-height:14rem;overflow:auto;font-size:12px;white-space:pre-wrap}
details{margin:.2rem 0}summary{cursor:pointer;color:#8ac}
</style>
<h1>orch</h1>
<div class=bar><span id=status></span><span>wave <b id=wave></b></span><span>budget left <b id=budget></b></span>
<button onclick="fetch('/stop',{method:'POST'})">stop after wave</button></div>
<table><thead><tr><th>step<th>task<th>owns<th>needs<th>agent<th>status<th>secs</thead><tbody id=steps></tbody></table>
<h1>agent scoreboard (cost per pass, lower first)</h1><table><tbody id=stats></tbody></table>
<h1>log</h1><pre id=log></pre>
<script>
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[<&]/g,c=>({'<':'&lt;','&':'&amp;'}[c]));
async function tick(){
  const st=await (await fetch('/state')).json(), sc=await (await fetch('/stats')).json();
  $('status').textContent=st.status; $('wave').textContent=st.wave; $('budget').textContent=st.budget;
  $('steps').innerHTML=Object.entries(st.steps).map(([id,s])=>`<tr><td>${esc(id)}<td>${esc(s.task)}<td>${esc((s.owns||[]).join(' '))}<td>${esc((s.needs||[]).join(' '))}<td>${esc(s.agent)}<td class=${s.status}>${s.status}${s.tail?`<details><summary>output</summary><pre>${esc(s.tail)}</pre></details>`:''}${s.diff?`<div><a href="/diff/${esc(id)}" target=_blank>diff</a></div>`:''}${(s.out_of_bounds||[]).length?`<div class=failed>out of bounds: ${esc(s.out_of_bounds.join(' '))}</div>`:''}<td>${esc(s.secs)}</tr>`).join('');
  $('stats').innerHTML=Object.entries(sc).map(([k,v])=>`<tr><td>${esc(k)}<td>${v.pass} pass<td>${v.fail} fail</tr>`).join('');
  $('log').textContent=(st.log||[]).slice().reverse().join('\\n');
}
tick(); setInterval(tick,2000);
</script>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, body, ctype="application/json"):
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.end_headers(); self.wfile.write(body.encode())

    def do_GET(self):
        if self.path in FILES:
            f = FILES[self.path]
            return self._send(f.read_text() if f.exists() else "{}")
        if self.path.startswith("/diff/"):
            sid = self.path[6:]
            f = RUNS / f"{sid}.diff"
            if "/" in sid or ".." in sid or not f.exists():     # no path tricks
                self.send_response(404); self.end_headers(); return
            return self._send(f.read_text(), "text/plain; charset=utf-8")
        self._send(PAGE, "text/html")

    def do_POST(self):
        if self.path == "/stop":
            STOP.touch()
        self._send("{}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://127.0.0.1:{port}")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
