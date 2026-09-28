"""Approval gate hardening (offline: in-memory store, temp settings / access log, ephemeral port):
per-session token + Origin on every POST, answered_by from the session, audit fields, access log,
and a guard that no code reads password files or sets libpq password env vars."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

import keeper_dashboard as app
from dashboard_client import TOKEN_RE, auth, base, new_session
from matrix import config
from matrix import keeper_dashboard as kd
from matrix import keeper_settings
from matrix.keeper_store import MemoryStore


def waiting_job(id=1):
    return {"id": id, "message": "Send it?", "tasks": [], "agents": [], "drafts": [], "created_at": None,
            "approvals": [{"type": "yes_no", "from": "BEZEL", "to": "owner", "text": "Send?", "status": "pending",
                           "at": "2026-09-27T10:00:00Z"}],
            "step_log": [{"tag": "approval asked", "approval": 0, "at": "2026-09-27T10:00:00Z"}]}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEPER_SETTINGS_FILE", str(tmp_path / "keeper_settings.json"))
    monkeypatch.setenv("KEEPER_ACCESS_LOG", str(tmp_path / "logs" / "keeper_access.log"))
    monkeypatch.setattr(config, "ANSWER_GRACE_SECONDS", 30.0)


@pytest.fixture
def server():
    store = MemoryStore([waiting_job(1), waiting_job(2)])
    srv = app.make_server(0, app.Backend(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, store
    srv.shutdown()
    srv.server_close()
    for h in app.access_logger().handlers:
        h.flush()


def post(srv, path, body, headers, ua="GateTest/1.0"):
    req = urllib.request.Request(base(srv) + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": ua, **headers})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def access_log_text():
    for h in app.access_logger().handlers:
        h.flush()
    path = app.access_log_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


ANSWER = {"job": 1, "index": 0, "action": "yes"}


# ---------- token + Origin ----------
def test_page_sets_httponly_strict_cookie_and_embeds_token(server):
    srv, _ = server
    r = urllib.request.urlopen(base(srv) + "/", timeout=5)
    cookie = r.headers.get("Set-Cookie")
    assert cookie.startswith("keeper_session=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    page = r.read().decode()
    token = TOKEN_RE.search(page).group(1)
    assert token and token not in cookie                                # separate random values
    assert '"X-Keeper-Token": KEEPER_TOKEN' in page and "jsonHeaders()" in page
    assert 'class="approve"' in urllib.request.urlopen(base(srv) + "/job/1", timeout=5).read().decode()


def test_missing_or_wrong_token_or_origin_is_403(server):
    srv, store = server
    good = new_session(srv)
    no_token = {k: v for k, v in good.items() if k != "X-Keeper-Token"}
    no_cookie = {k: v for k, v in good.items() if k != "Cookie"}
    no_origin = {k: v for k, v in good.items() if k != "Origin"}
    other = new_session(srv)
    cases = {
        "nothing": {},
        "no token": no_token,
        "no cookie": no_cookie,
        "wrong token": {**good, "X-Keeper-Token": "x" * 43},
        "token of another session": {**good, "X-Keeper-Token": other["X-Keeper-Token"]},
        "unknown cookie": {**good, "Cookie": "keeper_session=forged"},
        "no origin": no_origin,
        "wrong origin": {**good, "Origin": "http://evil.example"},
        "other port origin": {**good, "Origin": "http://127.0.0.1:1"},
    }
    for name, headers in cases.items():
        for path, body in (("/api/answer", ANSWER), ("/api/undo", {"job": 1, "index": 0}),
                           ("/api/change-to-no", {"job": 1, "index": 0}), ("/api/change-to-yes", {"job": 1}),
                           ("/api/reopen", {"job": 1}), ("/api/self-build", {"on": False}),
                           ("/api/shutdown-task", {"task": "self-build"}), ("/api/worker", {"on": False}),
                           ("/api/pause", {"job": 1, "paused": True})):
            assert post(srv, path, body, headers)[0] == 403, (name, path)
    assert store.rows[1]["approvals"][0]["status"] == "pending"          # nothing was answered
    assert keeper_settings.is_self_build_on() and keeper_settings.worker_on()
    assert kd.job_status(store.rows[1]) == kd.WAITING
    log = access_log_text()
    assert "REJECTED POST /api/answer" in log and "missing or foreign Origin" in log
    assert "wrong session token" in log
    assert good["X-Keeper-Token"] not in log and good["Cookie"].split("=", 1)[1] not in log


def test_right_token_and_origin_answers_with_session_and_audit(server):
    srv, store = server
    headers = auth(srv)
    code, body = post(srv, "/api/answer", ANSWER, headers, ua="Mozilla/5.0 GateTest")
    assert code == 200
    a = store.rows[1]["approvals"][0]
    assert a["status"] == "yes"
    assert a["answered_by"].startswith("owner (dashboard session ") and len(a["answered_by"]) > 26
    audit = a["audit"]
    assert audit["ip"] == "127.0.0.1" and audit["user_agent"] == "Mozilla/5.0 GateTest"
    assert audit["at"].endswith("Z") and audit["session"] in a["answered_by"]
    # Undo + Change carry the session and audit too
    code, body = post(srv, "/api/change-to-no", {"job": 1, "index": 0}, headers)
    assert code == 200 and body["approval"]["answered_by"] == a["answered_by"] and body["approval"]["audit"]["ip"]
    code, _ = post(srv, "/api/undo", {"job": 1, "index": 0}, headers)
    assert code == 200
    step = store.rows[1]["step_log"][-1]
    assert step["by"] == a["answered_by"] and step["audit"]["user_agent"] == "GateTest/1.0"
    # switches record the session as well
    assert post(srv, "/api/worker", {"on": True}, headers)[0] == 200
    assert keeper_settings.load()["changed_by"] == a["answered_by"]
    log = access_log_text()
    assert "APPROVAL /api/answer job=1 index=0 action=yes" in log and "Mozilla/5.0 GateTest" in log
    assert headers["X-Keeper-Token"] not in log and headers["Cookie"].split("=", 1)[1] not in log


def test_access_log_writes_lines_without_token_or_cookie(server):
    srv, _ = server
    headers = new_session(srv)
    urllib.request.urlopen(base(srv) + "/api/jobs?x=1", timeout=5).read()
    post(srv, "/api/answer", {"job": 2, "index": 0, "action": "no"}, headers)
    log = access_log_text()
    assert '127.0.0.1 "GET /api/jobs" 200' in log and '"POST /api/answer" 200' in log and "x=1" not in log
    assert headers["X-Keeper-Token"] not in log and "keeper_session=" not in log
    assert str(app.access_log_path()).endswith("keeper_access.log")
    assert "logs" in str(app.access_log_path().parent) and os.environ["KEEPER_ACCESS_LOG"]


def test_expired_session_is_refused():
    now = [1000.0]
    sessions = app.Sessions(ttl=60, clock=lambda: now[0])
    sid, s = sessions.new()
    assert sessions.check(sid, s["token"])["short"] == s["short"]
    assert sessions.check(sid, "nope") is None and sessions.check(None, s["token"]) is None
    now[0] += 61
    assert sessions.check(sid, s["token"]) is None and sessions.get(sid) is None


def test_log_text_is_cleaned():
    assert app.clean("a\r\nFAKE LINE\x00") == "a??FAKE LINE?"


def test_kd_defaults_unchanged_for_direct_calls():
    store = MemoryStore([waiting_job(3)])
    kd.answer_approval(store, 3, 0, "yes")
    a = store.rows[3]["approvals"][0]
    assert a["answered_by"] == kd.ANSWERED_BY and "audit" not in a


# ---------- no secret leaks in code (connection code left unchanged) ----------
def test_no_secret_files_or_inline_passwords_in_code():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for folder, _, files in os.walk(root):
        if any(part in folder for part in (".venv", ".git", "logs", "__pycache__", os.sep + "tests")):
            continue
        for f in files:
            if f.endswith((".py", ".bat", ".ps1", ".sh")):
                text = open(os.path.join(folder, f), encoding="utf-8", errors="ignore").read()
                assert "pw.txt" not in text and "PGPASSWORD" not in text, f
