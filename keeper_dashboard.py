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
Main brain: the header shows the main brain (read live from .env), a Gemini / xAI (Grok) switch that
rewrites ONLY the LLM_PROVIDER= line (backup in logs/, atomic replace; the worker picks it up within
about 10 seconds), a BEZEL Grok / Gemini switch that rewrites ONLY the BRAIN_BEZEL= line (added if missing),
each agent's brain (BEZEL and STEWARD pinned) and the worker's last `brains:` log line.
Switches: the header has the global Keeper worker On/Off switch (logs/keeper_settings.json; Off =
the worker stays alive but claims nothing) and each open job has its own On/Paused switch
(`paused` / `resumed` step_log entries; the worker skips paused jobs). The Keeper worker waits out the grace window
(KEEPER_ANSWER_GRACE_SECONDS, default 30), then logs `approval answered` and closes or continues.
Self-build loop: a separate "Self-build loop: ON/OFF" header toggle (POST /api/self-build) and an
"Always-running tasks" card whose "Shut down task" button (POST /api/shutdown-task) sets self_build_on
false in the same settings file. Neither ever answers, closes or rejects a job or approval.
GET /api/jobs and /api/job/<id> return JSON (for later tools).

Approval gate: every page load gives your browser a session (a random id in an HttpOnly,
SameSite=Strict cookie) and puts a matching random token in the page; the page sends it back as the
X-Keeper-Token header. Every POST (answer, Undo, Change to Yes/No, Reopen, pause, switches, brains)
needs that cookie + token pair AND an Origin header of this dashboard, on top of the Host allow-list,
JSON-only and size checks; anything else gets 403 and is logged. Sessions live only in this
process's memory and expire (KEEPER_SESSION_TTL_SECONDS, default 12 hours); restarting the dashboard
just means reloading the page. Answers record answered_by "owner (dashboard session <id>)" plus an
audit entry (time, client IP, user agent). Requests are logged to logs/keeper_access.log (rotating,
KEEPER_ACCESS_LOG to move it); tokens and cookies are never logged.
"""
import argparse
import hmac
import json
import logging
import logging.handlers
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from matrix import brain_switch, config
from matrix import keeper_dashboard as kd
from matrix import keeper_settings
from matrix.envelope import utc_now

HOST = "127.0.0.1"
DEFAULT_PORT = 8766
PORT_TRIES = 10
MAX_BODY = 20_000


COOKIE_NAME = "keeper_session"
TOKEN_HEADER = "X-Keeper-Token"
SESSION_TTL_SECONDS = float(os.getenv("KEEPER_SESSION_TTL_SECONDS", str(12 * 3600)))
MAX_SESSIONS = 200
DEFAULT_ACCESS_LOG = Path(__file__).resolve().parent / "logs" / "keeper_access.log"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean(value, limit: int = 300) -> str:
    """Log-safe text: no control characters (no forged log lines), capped length."""
    return _CONTROL.sub("?", "" if value is None else str(value))[:limit]


def access_log_path() -> Path:
    return Path(os.getenv("KEEPER_ACCESS_LOG") or DEFAULT_ACCESS_LOG)


def access_logger() -> logging.Logger:
    """logs/keeper_access.log: a rotating file (1 MB x 5 kept, nothing wiped on a schedule).
    Never given tokens, cookies or secrets."""
    lg = logging.getLogger("keeper_access")
    path = str(access_log_path())
    if not any(getattr(h, "baseFilename", None) == os.path.abspath(path) for h in lg.handlers):
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(fh)
        lg.setLevel(logging.INFO)
        lg.propagate = False
    return lg


class Sessions:
    """Dashboard browser sessions, in memory only: session id (cookie) -> token (in the page).
    Random values from secrets.token_urlsafe, checked with hmac.compare_digest, with an expiry."""

    def __init__(self, ttl: float = SESSION_TTL_SECONDS, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float):
        for sid in [s for s, v in self._items.items() if v["expires"] <= now]:
            del self._items[sid]
        while len(self._items) >= MAX_SESSIONS:  # oldest first
            del self._items[min(self._items, key=lambda s: self._items[s]["expires"])]

    def new(self) -> tuple[str, dict]:
        with self._lock:
            now = self.clock()
            self._prune(now)
            sid = secrets.token_urlsafe(32)
            self._items[sid] = {"token": secrets.token_urlsafe(32), "short": secrets.token_hex(4),
                                "expires": now + self.ttl}
            return sid, dict(self._items[sid])

    def get(self, sid: str | None) -> dict | None:
        """The live session for this cookie value (its expiry is extended), or None."""
        if not sid:
            return None
        with self._lock:
            now = self.clock()
            item = self._items.get(sid)
            if item is None or item["expires"] <= now:
                self._items.pop(sid, None)
                return None
            item["expires"] = now + self.ttl
            return dict(item)

    def check(self, sid: str | None, token: str | None) -> dict | None:
        """The session if the cookie is live AND the token matches it (constant-time), else None."""
        if not sid or not token:
            return None
        with self._lock:
            item = self._items.get(sid)
            if item is None or item["expires"] <= self.clock():
                return None
            if not hmac.compare_digest(item["token"].encode(), str(token).encode()):
                return None
            return dict(item)


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
    sessions: Sessions = None  # set by make_server
    _cookie_out: str | None = None

    def log_message(self, fmt, *args):
        """Access log line: client IP, method + path (no query string), status, user agent.
        Never the token, cookies or request bodies. Nothing goes to the window."""
        try:
            path = (self.path or "").split("?", 1)[0] if getattr(self, "path", None) else ""
            what = f"{self.command} {path}" if getattr(self, "command", None) else ""
            detail = fmt % args if not what else " ".join(str(a) for a in args[1:])
            ua = self.headers.get("User-Agent", "") if getattr(self, "headers", None) else ""
            access_logger().info('%s "%s" %s ua="%s"', clean(self.client_address[0]), clean(what),
                                 clean(detail), clean(ua))
        except Exception:  # noqa: BLE001 - logging must never break a request
            pass

    def _audit_log(self, level: int, text: str, *args):
        try:
            access_logger().log(level, text, *[clean(a) for a in args])
        except Exception:  # noqa: BLE001
            pass

    def _cookie_sid(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            morsel = SimpleCookie(raw).get(COOKIE_NAME)
        except CookieError:
            return None
        return morsel.value if morsel else None

    def _page_session(self) -> dict:
        """The browser's session (new one + Set-Cookie if it has none or it expired)."""
        sess = self.sessions.get(self._cookie_sid())
        if sess is None:
            sid, sess = self.sessions.new()
            self._cookie_out = (f"{COOKIE_NAME}={sid}; Path=/; HttpOnly; SameSite=Strict; "
                                f"Max-Age={int(self.sessions.ttl)}")
        return sess

    def _page(self, code: int, text: str):
        sess = self._page_session()
        return self._html(code, kd.with_token(text, sess["token"]))

    def _origin_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Origin") in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _reject(self, path: str, reason: str):
        self._audit_log(logging.WARNING, "REJECTED POST %s from %s: %s (ua=\"%s\")", path,
                        self.client_address[0], reason, self.headers.get("User-Agent", ""))
        return self._json(403, {"error": "Forbidden. Reload the dashboard page and try again."})

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
        if self._cookie_out:
            self.send_header("Set-Cookie", self._cookie_out)
            self._cookie_out = None
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
                return self._page(200, kd.render_list(self.backend.run(kd.list_jobs), settings=keeper_settings.load(),
                                                     brain=brain_switch.panel_info()))
            if path == "/api/jobs":
                jobs = self.backend.run(kd.list_jobs)
                return self._json(200, {"summary": kd.summary(jobs), "settings": keeper_settings.load(),
                                        "jobs": [{**j, "status": kd.job_status(j)} for j in jobs]})
            if m:
                job = self.backend.run(kd.get_job, int(m.group(2)))
                if m.group(1):
                    return self._json(404, {"error": "Not found"}) if job is None else \
                        self._json(200, {**job, "status": kd.job_status(job)})
                return self._page(404, kd.render_not_found(m.group(2))) if job is None else \
                    self._page(200, kd.render_job(job, settings=keeper_settings.load(),
                                                 brain=brain_switch.panel_info()))
        except NoDatabase as err:
            if path.startswith("/api/"):
                return self._json(503, {"error": str(err)})
            return self._page(503, kd.render_error("Keeper database not available", str(err)))
        return self._json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._allowed_host():
            return self._json(403, {"error": "Forbidden"})
        path = self.path.split("?", 1)[0]
        if not self._origin_ok():  # must be present AND be this dashboard
            return self._reject(path, "missing or foreign Origin")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(415, {"error": "Send JSON"})
        if path not in ("/api/answer", "/api/undo", "/api/reopen", "/api/change-to-yes", "/api/change-to-no",
                        "/api/pause", "/api/worker", "/api/brain", "/api/bezel-brain",
                        "/api/self-build", "/api/shutdown-task"):
            return self._json(404, {"error": "Not found"})
        sess = self.sessions.check(self._cookie_sid(), self.headers.get(TOKEN_HEADER))
        if sess is None:  # every POST needs this page's session cookie + matching token
            return self._reject(path, "missing, expired or wrong session token")
        by = kd.session_label(sess["short"])
        audit = {"at": utc_now(), "ip": clean(self.client_address[0], 64),
                 "user_agent": clean(self.headers.get("User-Agent", "")), "session": sess["short"]}
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "Too big"})
        try:
            data = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))
            if path == "/api/brain":  # rewrites ONLY the LLM_PROVIDER= line in .env (gemini or xai)
                result = brain_switch.set_main_brain(data.get("provider"))
                return self._json(200, {"ok": True, "main": result["now"], "previous": result["previous"]})
            if path == "/api/bezel-brain":  # rewrites ONLY the BRAIN_BEZEL= line (added if missing)
                result = brain_switch.set_bezel_brain(data.get("provider"))
                return self._json(200, {"ok": True, "bezel": result["now"], "previous": result["previous"]})
            if path == "/api/worker":
                if not isinstance(data.get("on"), bool):
                    raise ValueError
                return self._json(200, {"ok": True, "settings": keeper_settings.set_worker_on(data["on"], by=by)})
            if path == "/api/self-build":  # settings file only; never touches a job or approval
                if not isinstance(data.get("on"), bool):
                    raise ValueError
                return self._json(200, {"ok": True, "settings": keeper_settings.set_self_build_on(data["on"], by=by)})
            if path == "/api/shutdown-task":  # stop an always-running task; not a rejection
                if data.get("task") != kd.SELF_BUILD_TASK:
                    return self._json(404, {"error": "No such always-running task."})
                return self._json(200, {"ok": True, "settings": keeper_settings.shut_down_self_build(by=by)})
            job_id = data["job"]
            if path == "/api/pause":
                if not isinstance(data.get("paused"), bool) or not isinstance(job_id, int) or isinstance(job_id, bool):
                    raise ValueError
                paused = self.backend.run(lambda s: kd.set_paused(s, job_id, data["paused"], by=by))
                return self._json(200, {"ok": True, "paused": paused})
            optional = path in ("/api/reopen", "/api/change-to-yes", "/api/change-to-no")  # index may be left out
            index = data.get("index") if optional else data["index"]
            is_int = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
            if not is_int(job_id) or not (is_int(index) or (index is None and optional)):
                raise ValueError
            action, text = str(data.get("action", "")), str(data.get("text", "") or "")
        except (kd.AnswerError, brain_switch.BrainSwitchError) as err:  # (ValueErrors, so they must come first)
            return self._json(err.code, {"error": str(err)})
        except NoDatabase as err:
            return self._json(503, {"error": str(err)})
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
            return self._json(400, {"error": "That wasn't valid data"})
        except OSError as err:  # settings file couldn't be written
            return self._json(500, {"error": f"Couldn't save the switch ({type(err).__name__})."})
        try:
            if path == "/api/answer":
                item = self.backend.run(lambda s: kd.answer_approval(s, job_id, index, action, text,
                                                                     by=by, audit=audit))
            else:
                mode = {"/api/undo": kd.UNDO, "/api/reopen": kd.REOPEN,
                        "/api/change-to-yes": kd.CHANGE_TO_YES, "/api/change-to-no": kd.CHANGE_TO_NO}[path]
                item = self.backend.run(lambda s: kd.reopen_approval(s, job_id, index, mode, by=by, audit=audit))
        except kd.AnswerError as err:
            return self._json(err.code, {"error": str(err)})
        except NoDatabase as err:
            return self._json(503, {"error": str(err)})
        self._audit_log(logging.INFO, "APPROVAL %s job=%s index=%s action=%s by=%s ip=%s ua=\"%s\"", path, job_id,
                        index, action if path == "/api/answer" else "-", by, audit["ip"], audit["user_agent"])
        return self._json(200, {"ok": True, "approval": item})


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR would let two programs share a port, so a busy port wouldn't be noticed.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def make_server(port: int, backend: Backend | None = None) -> Server:
    access_logger()
    handler = type("BoundHandler", (Handler,), {"backend": backend or Backend(), "sessions": Sessions()})
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
