"""Keeper dashboard: see every Keeper job and answer approvals, in your browser.

Run:  python keeper_dashboard.py            (opens http://127.0.0.1:8766 in your browser)
      python keeper_dashboard.py --port 9000 --no-browser
Stop: press Ctrl+C in the Command Prompt window.

Only reachable from this computer (bound to 127.0.0.1). No extra installs needed.
Reads the `jobs` table through CHECKPOINT_DB_URL in .env. The only thing it ever changes is one
approval: its status (and answer text) when you click Approve / Reject / Send answer, or back to
pending (plus an `approval reopened` step) when you click Undo (within the grace window, before the
worker acts) or Reopen (on a job your Reject closed). Change to Yes (after a Reject) logs
`approval reopened` and sets the answer to yes with a fresh answered_at; Change to No (after an
Approve, e.g. on a job that closed) does the same with no, and the worker then marks it rejected.
Switches: the header has the global Keeper worker On/Off switch (logs/keeper_settings.json; Off =
the worker stays alive but claims nothing) and each open job has its own On/Paused switch
(`paused` / `resumed` step_log entries; the worker skips paused jobs). The Keeper worker waits out the grace window
(KEEPER_ANSWER_GRACE_SECONDS, default 30), then logs `approval answered` and closes or continues.
GET /api/jobs and /api/job/<id> return JSON (for later tools).
"""
import argparse
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from matrix import config
from matrix import keeper_dashboard as kd
from matrix import keeper_settings

HOST = "127.0.0.1"
DEFAULT_PORT = 8766
PORT_TRIES = 10
MAX_BODY = 20_000


class NoDatabase(Exception):
    """CHECKPOINT_DB_URL unset or Postgres unreachable. The message is safe to show (no password)."""


class Backend:
    """Holds one PgStore; opens it on first use and again after a connection problem."""

    def __init__(self, store=None):
        self._store = store
        self._lock = threading.Lock()

    def store(self):
        with self._lock:
            if self._store is None:
                if not config.CHECKPOINT_DB_URL:
                    raise NoDatabase("CHECKPOINT_DB_URL isn't set in .env, so there's no Keeper database to show. "
                                     "See docs/KEEPER_SETUP.md, then restart the dashboard.")
                from matrix.keeper_store import PgStore
                try:
                    self._store = PgStore(config.CHECKPOINT_DB_URL)
                except Exception as exc:  # noqa: BLE001 - never echo the URL (it holds the password)
                    raise NoDatabase(f"Can't reach the Keeper database ({type(exc).__name__}). "
                                     "Is PostgreSQL running? This page will keep trying.") from None
            return self._store

    def reset(self):
        with self._lock:
            if self._store is not None and hasattr(self._store, "conn"):
                try:
                    self._store.close()
                except Exception:  # noqa: BLE001
                    pass
            if hasattr(self._store, "conn"):
                self._store = None

    def run(self, fn, *args):
        """fn(store, *args), turning database trouble into NoDatabase."""
        store = self.store()
        try:
            return fn(store, *args)
        except kd.AnswerError:
            raise
        except Exception as exc:  # noqa: BLE001
            if type(exc).__module__.startswith("psycopg"):
                self.reset()
                raise NoDatabase(f"Lost the connection to the Keeper database ({type(exc).__name__}). "
                                 "This page will keep trying.") from None
            raise


class Handler(BaseHTTPRequestHandler):
    server_version = "KeeperDashboard/1.0"
    backend: Backend = None  # set by make_server

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
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, text: str):
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if not self._allowed_host():
            return self._json(403, {"error": "Forbidden"})
        path = self.path.split("?", 1)[0]
        m = re.fullmatch(r"/(api/)?job/(\d{1,12})", path)
        try:
            if path in ("/", "/index.html"):
                return self._html(200, kd.render_list(self.backend.run(kd.list_jobs), settings=keeper_settings.load()))
            if path == "/api/jobs":
                jobs = self.backend.run(kd.list_jobs)
                return self._json(200, {"summary": kd.summary(jobs), "settings": keeper_settings.load(),
                                        "jobs": [{**j, "status": kd.job_status(j)} for j in jobs]})
            if m:
                job = self.backend.run(kd.get_job, int(m.group(2)))
                if m.group(1):
                    return self._json(404, {"error": "Not found"}) if job is None else \
                        self._json(200, {**job, "status": kd.job_status(job)})
                return self._html(404, kd.render_not_found(m.group(2))) if job is None else \
                    self._html(200, kd.render_job(job, settings=keeper_settings.load()))
        except NoDatabase as err:
            if path.startswith("/api/"):
                return self._json(503, {"error": str(err)})
            return self._html(503, kd.render_error("Keeper database not available", str(err)))
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
        path = self.path.split("?", 1)[0]
        if path not in ("/api/answer", "/api/undo", "/api/reopen", "/api/change-to-yes", "/api/change-to-no",
                        "/api/pause", "/api/worker"):
            return self._json(404, {"error": "Not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "Too big"})
        try:
            data = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))
            if path == "/api/worker":
                if not isinstance(data.get("on"), bool):
                    raise ValueError
                return self._json(200, {"ok": True, "settings": keeper_settings.set_worker_on(data["on"])})
            job_id = data["job"]
            if path == "/api/pause":
                if not isinstance(data.get("paused"), bool) or not isinstance(job_id, int) or isinstance(job_id, bool):
                    raise ValueError
                paused = self.backend.run(kd.set_paused, job_id, data["paused"])
                return self._json(200, {"ok": True, "paused": paused})
            optional = path in ("/api/reopen", "/api/change-to-yes", "/api/change-to-no")  # index may be left out
            index = data.get("index") if optional else data["index"]
            is_int = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
            if not is_int(job_id) or not (is_int(index) or (index is None and optional)):
                raise ValueError
            action, text = str(data.get("action", "")), str(data.get("text", "") or "")
        except kd.AnswerError as err:  # (a ValueError, so it must come first)
            return self._json(err.code, {"error": str(err)})
        except NoDatabase as err:
            return self._json(503, {"error": str(err)})
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
            return self._json(400, {"error": "That wasn't valid data"})
        except OSError as err:  # settings file couldn't be written
            return self._json(500, {"error": f"Couldn't save the switch ({type(err).__name__})."})
        try:
            if path == "/api/answer":
                item = self.backend.run(kd.answer_approval, job_id, index, action, text)
            else:
                mode = {"/api/undo": kd.UNDO, "/api/reopen": kd.REOPEN,
                        "/api/change-to-yes": kd.CHANGE_TO_YES, "/api/change-to-no": kd.CHANGE_TO_NO}[path]
                item = self.backend.run(kd.reopen_approval, job_id, index, mode)
        except kd.AnswerError as err:
            return self._json(err.code, {"error": str(err)})
        except NoDatabase as err:
            return self._json(503, {"error": str(err)})
        return self._json(200, {"ok": True, "approval": item})


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR would let two programs share a port, so a busy port wouldn't be noticed.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def make_server(port: int, backend: Backend | None = None) -> Server:
    handler = type("BoundHandler", (Handler,), {"backend": backend or Backend()})
    return Server((HOST, port), handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Keeper dashboard for the Cross Coat Matrix")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    args = ap.parse_args(argv)
    server = None
    for port in range(args.port, args.port + PORT_TRIES):
        try:
            server = make_server(port)
            break
        except OSError:
            continue
    if server is None:
        sys.exit(f"Ports {args.port}-{args.port + PORT_TRIES - 1} are all busy. Is the dashboard already open?")
    url = f"http://{HOST}:{server.server_address[1]}"
    print(f"Keeper dashboard is open at {url}  (press Ctrl+C here to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.RequestHandlerClass.backend.reset()
        print("Keeper dashboard stopped.")


if __name__ == "__main__":
    main()
