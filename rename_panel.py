"""Rename panel: change what your assistant and agents are called.

Run:  python rename_panel.py            (opens http://127.0.0.1:8765 in your browser)
      python rename_panel.py --port 9000 --no-browser
Stop: press Ctrl+C in the Command Prompt window.

Only reachable from this computer (bound to 127.0.0.1). No extra installs needed.
Saves to matrix_names.json. GET /names returns the same data as JSON (for the future 3D view).
"""
import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from matrix import names

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY = 200_000

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cross Coat Matrix - Rename panel</title>
<style>
  :root { --bg:#f4f6fb; --card:#fff; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--ink); }
  header { background:#111827; color:#fff; padding:18px 28px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  header h1 { font-size:20px; margin:0; flex:1; }
  header p { margin:0; color:#cbd5e1; font-size:14px; width:100%; }
  main { max-width:1100px; margin:0 auto; padding:20px 28px 60px; }
  h2 { font-size:17px; margin:28px 0 10px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
       background:#f9fafb; padding:10px 12px; border-bottom:1px solid var(--line); }
  td { padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:middle; }
  tr:last-child td { border-bottom:none; }
  input[type=text] { width:100%; padding:7px 9px; border:1px solid #d1d5db; border-radius:7px; font-size:14px; font-family:inherit; }
  input[type=text]:focus { outline:2px solid #bfdbfe; border-color:var(--blue); }
  input.icon { width:56px; text-align:center; font-size:18px; }
  input[type=color] { width:42px; height:32px; border:1px solid #d1d5db; border-radius:7px; padding:2px; background:#fff; cursor:pointer; }
  .id { font-family:Consolas,monospace; font-size:12px; color:var(--muted); }
  .kind { display:inline-block; font-size:11px; padding:2px 8px; border-radius:99px; background:#eef2ff; color:#3730a3; margin-top:3px; }
  .kind.assistant { background:#dbeafe; color:#1e40af; } .kind.router { background:#f3e8ff; color:#6b21a8; }
  .kind.senior { background:#ffedd5; color:#9a3412; } .kind.connected { background:#dcfce7; color:#166534; }
  .kind.planned { background:#f3f4f6; color:#4b5563; } .kind.off { background:#fee2e2; color:#991b1b; }
  button { font:inherit; font-size:14px; padding:9px 18px; border-radius:8px; border:1px solid transparent; cursor:pointer; }
  .save { background:var(--blue); color:#fff; } .save:hover { background:#1d4ed8; }
  .reset { background:#fff; color:#b91c1c; border-color:#fecaca; } .reset:hover { background:#fef2f2; }
  #msg { margin:14px 0 0; padding:10px 14px; border-radius:8px; display:none; font-size:14px; white-space:pre-line; }
  #msg.ok { display:block; background:#dcfce7; color:#166534; } #msg.err { display:block; background:#fee2e2; color:#991b1b; }
  .bar { display:flex; gap:10px; margin-top:22px; }
  .hint { color:var(--muted); font-size:13px; margin:4px 0 0; }
</style></head>
<body>
<header><h1>Rename panel</h1>
  <button class="reset" onclick="resetAll()">Reset to defaults</button>
  <button class="save" onclick="saveAll()">Save</button>
  <p>Change what your assistant and agents are called. Names must be different from each other, 1 to 40 characters. The inside ID never changes.</p>
</header>
<main>
  <div id="msg"></div>
  <h2>Assistant and agents</h2>
  <p class="hint">The first row is the name you talk to in chat. The rest show up in routing lines (SHOW_ROUTING=1) and later in the 3D view.</p>
  <div class="card"><table><thead><tr><th style="width:170px">Inside ID</th><th style="width:70px">Icon</th>
    <th style="width:230px">Name</th><th>Description</th><th style="width:60px">Color</th></tr></thead>
    <tbody id="agents"></tbody></table></div>
  <h2>Services</h2>
  <p class="hint">Outside services the Matrix uses (connected) or will use later (planned).</p>
  <div class="card"><table><thead><tr><th style="width:170px">Inside ID</th><th style="width:70px">Icon</th>
    <th style="width:230px">Name</th><th>Description</th><th style="width:60px">Color</th></tr></thead>
    <tbody id="services"></tbody></table></div>
  <div class="bar"><button class="save" onclick="saveAll()">Save</button>
    <button class="reset" onclick="resetAll()">Reset to defaults</button></div>
</main>
<script>
let data = null;
function el(tag, props, kids) { const e = document.createElement(tag); Object.assign(e, props || {}); (kids || []).forEach(k => e.append(k)); return e; }
function row(entry) {
  const badge = entry.kind === "service" ? entry.status : entry.kind;
  const idCell = el("td", {}, [el("div", {className:"id", textContent:entry.id}), el("span", {className:"kind " + badge, textContent:badge})]);
  const input = (field, extra) => { const i = el("input", Object.assign({type:"text", value:entry[field] || ""}, extra || {}));
    i.oninput = () => { entry[field] = i.value; }; return i; };
  const color = el("input", {type:"color", value:entry.color || "#888888", title:"Color for the 3D view"});
  color.oninput = () => { entry.color = color.value; };
  return el("tr", {}, [idCell, el("td", {}, [input("icon", {className:"icon", maxLength:8, title:"One emoji (Windows key + . opens the emoji picker)"})]),
    el("td", {}, [input("display", {maxLength:40})]), el("td", {}, [input("description", {maxLength:200})]), el("td", {}, [color])]);
}
function render() {
  for (const section of ["agents", "services"]) {
    const body = document.getElementById(section); body.replaceChildren(...data[section].map(row));
  }
}
function say(text, ok) { const m = document.getElementById("msg"); m.textContent = text; m.className = ok ? "ok" : "err"; window.scrollTo(0, 0); }
async function send(url, body) {
  const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const j = await r.json(); if (!r.ok) throw new Error((j.errors || [j.error || "Something went wrong"]).join("\n")); return j;
}
async function load() { data = await (await fetch("/names")).json(); render(); }
async function saveAll() {
  try { data = await send("/names", data); render(); say("Saved. Chat will use the new names (restart chat.py to see the new greeting).", true); }
  catch (e) { say("Not saved:\n" + e.message, false); }
}
async function resetAll() {
  if (!confirm("Put every name, description, color and icon back to the defaults?")) return;
  try { data = await send("/reset", {}); render(); say("Everything is back to the defaults.", true); }
  catch (e) { say("Reset failed:\n" + e.message, false); }
}
load().catch(e => say("Could not load names: " + e.message, false));
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "RenamePanel/1.0"

    def log_message(self, fmt, *args):  # keep the window quiet
        pass

    def _allowed_host(self) -> bool:
        """Only answer requests addressed to this computer (blocks DNS-rebinding tricks)."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._allowed_host():
            return self._json(403, {"error": "Forbidden"})
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/names":
            return self._json(200, names.load())
        if path == "/defaults":
            return self._json(200, names.defaults())
        return self._json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._allowed_host():
            return self._json(403, {"error": "Forbidden"})
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            return self._json(403, {"error": "Forbidden"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(415, {"error": "Send JSON"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "Too big"})
        raw = self.rfile.read(length) if length else b"{}"
        path = self.path.split("?", 1)[0]
        try:
            if path == "/names":
                return self._json(200, names.save(json.loads(raw.decode("utf-8"))))
            if path == "/reset":
                return self._json(200, names.reset())
        except names.NamesError as err:
            return self._json(400, {"errors": err.errors})
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"errors": ["That wasn't valid data"]})
        return self._json(404, {"error": "Not found"})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rename panel for the Cross Coat Matrix")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    args = ap.parse_args(argv)
    try:
        server = ThreadingHTTPServer((HOST, args.port), Handler)
    except OSError:
        sys.exit(f"Port {args.port} is busy. Is the panel already open? Try: python rename_panel.py --port 8766")
    url = f"http://{HOST}:{args.port}"
    print(f"Rename panel is open at {url}  (press Ctrl+C here to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Rename panel stopped.")


if __name__ == "__main__":
    main()
