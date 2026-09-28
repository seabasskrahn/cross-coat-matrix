"""Keeper dashboard: status badges, the Approve / Reject / answer write, and the HTML pages (offline)."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

import keeper_dashboard as app
from matrix import envelope as env
from matrix import keeper_dashboard as kd
from matrix.keeper_store import MemoryStore

NOW = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)  # 8:00 PM Sep 27 in Edmonton


def job(jid=1, tags=(), approvals=(), **extra):
    return {"id": jid, "message": f"job {jid}", "tasks": [], "agents": [], "drafts": [],
            "approvals": list(approvals), "step_log": [{"tag": t, "at": "2026-09-28T01:09:38Z"} for t in tags],
            **extra}


def yes_no(status="pending", **kw):
    return {"from": "system", "to": "user", "type": "yes_no", "text": "Approve these drafts?",
            "status": status, "at": "2026-09-28T01:09:38Z", **kw}


def question(status="pending", **kw):
    return {"from": "BEZEL", "to": "user", "type": "context_request", "text": "Which crew?",
            "status": status, "at": "2026-09-28T01:10:00Z", **kw}


# ---------- status ----------
@pytest.mark.parametrize("tags, approvals, expected", [
    ([], [], kd.WORKING),
    (["message received", "task created"], [], kd.WORKING),
    (["message received", "approval asked"], [yes_no()], kd.WAITING),
    (["approval asked"], [question()], kd.WAITING),
    (["approval asked", "approval answered"], [yes_no("yes")], kd.WORKING),
    (["draft saved", "job closed"], [], kd.CLOSED),
    (["approval answered", "rejected", "job closed"], [yes_no("no")], kd.CLOSED),
    (["job closed"], [yes_no()], kd.CLOSED),                     # closed wins
    (["task created", "dependency cycle"], [], kd.HALTED),
    (["draft saved", "circuit breaker"], [yes_no()], kd.HALTED),  # halted beats waiting
    (["error: brain failed"], [], kd.HALTED),
    (["circuit breaker", "picked up"], [], kd.WORKING),          # only the LAST tag halts
])
def test_job_status(tags, approvals, expected):
    assert kd.job_status(job(tags=tags, approvals=approvals)) == expected


def test_status_tolerates_missing_or_bad_fields():
    assert kd.job_status({"id": 9}) == kd.WORKING
    assert kd.job_status({"id": 9, "step_log": None, "approvals": "junk"}) == kd.WORKING


def test_summary_counts():
    jobs = [job(1, ["job closed"]), job(2, ["approval asked"], [yes_no()]), job(3), job(4, ["error x"])]
    assert kd.summary(jobs) == {kd.WAITING: 1, kd.WORKING: 1, kd.HALTED: 1, kd.CLOSED: 1}


# ---------- times ----------
def test_times_are_local_am_pm():
    assert kd.fmt_time("2026-09-28T01:09:38Z", now=NOW) == "7:09 PM"
    assert kd.fmt_time("2026-09-26T15:05:00Z", now=NOW) == "Sat Sep 26, 9:05 AM"
    assert kd.fmt_time(datetime(2026, 9, 27, 19, 2, 28), now=NOW) == "7:02 PM"  # naive = already local
    assert kd.fmt_time(None) == "" and kd.fmt_time("not a time") == ""


# ---------- approvals ----------
def test_apply_answer_yes_no():
    ch = kd.apply_answer([yes_no()], 0, "yes", now="2026-09-28T02:00:00Z")
    assert ch == {"status": "yes", "answered_at": "2026-09-28T02:00:00Z", "answered_by": kd.ANSWERED_BY}
    assert kd.apply_answer([yes_no()], 0, "no")["status"] == "no"
    with pytest.raises(kd.AnswerError):
        kd.apply_answer([yes_no()], 0, "answer", "hi")


def test_apply_answer_context_request():
    ch = kd.apply_answer([question()], 0, "answer", "  Crew B  ")
    assert ch["status"] == "answered" and ch["answer"] == "Crew B"
    assert env.approval_answer({**question(), **ch}) == "Crew B"  # the worker reads it back
    for action, text in (("answer", "   "), ("yes", ""), ("answer", "x" * (kd.MAX_ANSWER + 1))):
        with pytest.raises(kd.AnswerError):
            kd.apply_answer([question()], 0, action, text)


@pytest.mark.parametrize("approvals, index, code", [
    ([yes_no("yes")], 0, 409), ([question("answered")], 0, 409),
    ([yes_no()], 1, 404), ([yes_no()], -1, 404), ([], 0, 404), (["junk"], 0, 400),
])
def test_apply_answer_refuses(approvals, index, code):
    with pytest.raises(kd.AnswerError) as err:
        kd.apply_answer(approvals, index, "yes")
    assert err.value.code == code


def test_answer_approval_changes_only_that_entry():
    store = MemoryStore([job(1, ["approval asked"], [yes_no("yes"), yes_no(), question()],
                             drafts=[{"task": 1, "output": "x"}])])
    before = store.get(1)
    item = kd.answer_approval(store, 1, 1, "yes")
    after = store.get(1)
    assert item["status"] == "yes" and after["approvals"][1]["status"] == "yes"
    assert after["approvals"][0] == before["approvals"][0] and after["approvals"][2] == before["approvals"][2]
    assert after["step_log"] == before["step_log"]       # the worker logs `approval answered`, not us
    assert after["drafts"] == before["drafts"] and after["message"] == before["message"]
    assert kd.job_status(after) == kd.WAITING           # the question is still open
    kd.answer_approval(store, 1, 2, "answer", "Crew B")
    assert store.get(1)["approvals"][2]["answer"] == "Crew B"
    assert kd.job_status(store.get(1)) == kd.WORKING
    with pytest.raises(kd.AnswerError):
        kd.answer_approval(store, 1, 1, "no")           # can't flip an answered one
    with pytest.raises(kd.AnswerError):
        kd.answer_approval(store, 99, 0, "yes")


class FakeConn:
    """Records SQL so we can check the Postgres path locks the row and changes one entry."""

    def __init__(self, approvals):
        self.approvals, self.sql, self.in_tx = approvals, [], False

    def transaction(self):
        conn = self

        class Tx:
            def __enter__(self):
                conn.in_tx = True

            def __exit__(self, *a):
                conn.in_tx = False
        return Tx()

    def execute(self, query, params=()):
        text = query.as_string(None) if hasattr(query, "as_string") else str(query)
        self.sql.append((text, self.in_tx))
        conn = self

        class Cur:
            def fetchone(self):
                if text.startswith("SELECT"):
                    return {"approvals": conn.approvals}
                idx, changes = int(params[0]), params[2].obj
                conn.approvals[idx] = {**conn.approvals[idx], **changes}
                return {"item": conn.approvals[idx]}
        return Cur()


def test_answer_approval_postgres_path_locks_row():
    pytest.importorskip("psycopg")
    from psycopg import sql
    store = type("FakePg", (), {})()
    store.conn, store._mutex = FakeConn([yes_no()]), threading.RLock()
    store.table = sql.Identifier("public", "jobs")
    item = kd.answer_approval(store, 2, 0, "no")
    assert item["status"] == "no"
    (select, tx1), (update, tx2) = store.conn.sql
    assert "FOR UPDATE" in select and tx1 and tx2
    assert "jsonb_set(approvals" in update and '"public"."jobs"' in update


# ---------- HTML ----------
def test_render_list():
    jobs = [job(3, ["job closed"], created_at=datetime(2026, 9, 27, 19, 18)),
            job(2, ["approval asked"], [yes_no()], agents=[{"name": "BEZEL", "role": "writer"}],
                tasks=[{"num": 1, "title": "t"}], message="<script>alert(1)</script>")]
    page = kd.render_list(jobs, now=NOW)
    assert "Keeper dashboard" in page and "#3" in page and "#2" in page
    assert 'badge closed">Closed' in page and 'badge waiting">Waiting on you' in page
    assert "BEZEL (writer)" in page and "7:18 PM" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "10000" in page  # auto-refresh every 10 s
    assert '<b>1</b><span>Waiting on you' in page and '<b>2</b><span>All jobs' in page


def test_render_list_empty():
    assert "No jobs yet." in kd.render_list([])


def test_render_job_detail():
    j = job(1, ["message received", "agent assigned", "approval asked"], [yes_no(), question(), yes_no("no")],
            tasks=[{"num": 1, "title": "Design", "depends_on": [], "agent": {"name": "LUMEN", "role": "designer"}},
                   {"num": 2, "title": "Wire up", "depends_on": [1]}],
            drafts=[{"task": 1, "agent": "LUMEN", "role": "designer", "output": "Line 1\n  <b>Line 2</b>",
                     "at": "2026-09-28T01:05:00Z"}])
    j["step_log"][1].update(agent="LUMEN", role="designer", task=1)
    page = kd.render_job(j, now=NOW)
    assert "LUMEN (designer)" in page and "#1" in page and "Wire up" in page
    assert "<pre>Line 1\n  &lt;b&gt;Line 2&lt;/b&gt;</pre>" in page       # draft shown as-is
    assert page.count('class="approve"') == 1 and page.count('class="reject"') == 1
    assert "answer(1, 0, 'yes')" in page and "answer(1, 0, 'no')" in page
    assert 'id="ans-1"' in page and "answer(1, 1, 'answer')" in page
    assert "confirm(" in page                                            # before Reject
    assert "7:09 PM" in page and "approval asked" in page and "t-wait" in page
    assert 'badge waiting">Waiting on you' in page


def test_render_job_without_pending_has_no_buttons():
    page = kd.render_job(job(3, ["job closed"], [yes_no("yes")]), now=NOW)
    assert 'class="approve"' not in page and "<textarea" not in page
    assert "No drafts yet." in page and "No tasks yet." in page


def test_render_error():
    page = kd.render_error("Keeper database not available", "Is PostgreSQL running?")
    assert "Is PostgreSQL running?" in page


# ---------- the web server (MemoryStore, real HTTP on 127.0.0.1) ----------
@pytest.fixture
def server():
    store = MemoryStore([job(1, ["approval asked"], [yes_no()]), job(2, ["job closed"])])
    srv = app.make_server(0, app.Backend(store))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, store
    srv.shutdown()
    srv.server_close()


def call(srv, path, body=None, headers=None):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_server_pages_and_answer(server):
    srv, store = server
    code, page = call(srv, "/")
    assert code == 200 and "Waiting on you" in page and "#2" in page
    code, page = call(srv, "/job/1")
    assert code == 200 and 'class="approve"' in page
    assert call(srv, "/job/77")[0] == 404
    code, text = call(srv, "/api/jobs")
    assert code == 200 and json.loads(text)["summary"][kd.CLOSED] == 1
    code, text = call(srv, "/api/answer", {"job": 1, "index": 0, "action": "yes"})
    assert code == 200 and store.get(1)["approvals"][0]["status"] == "yes"
    assert call(srv, "/api/answer", {"job": 1, "index": 0, "action": "no"})[0] == 409
    assert call(srv, "/api/answer", {"job": "1", "index": 0, "action": "no"})[0] == 400


def test_server_blocks_other_sites(server):
    srv, store = server
    body = {"job": 1, "index": 0, "action": "yes"}
    assert call(srv, "/api/answer", body, {"Origin": "http://evil.example"})[0] == 403
    assert store.get(1)["approvals"][0]["status"] == "pending"


def test_no_database_url_shows_message(monkeypatch):
    monkeypatch.setattr(app.config, "CHECKPOINT_DB_URL", "")
    srv = app.make_server(0, app.Backend())
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        code, page = call(srv, "/")
        assert code == 503 and "CHECKPOINT_DB_URL" in page
        assert call(srv, "/api/answer", {"job": 1, "index": 0, "action": "yes"})[0] == 503
    finally:
        srv.shutdown()
        srv.server_close()


def test_unreachable_database_shows_message(monkeypatch):
    monkeypatch.setattr(app.config, "CHECKPOINT_DB_URL", "postgresql://x:secret@127.0.0.1:1/keeper?connect_timeout=2")
    with pytest.raises(app.NoDatabase) as err:
        app.Backend().store()
    assert "secret" not in str(err.value) and "Can't reach" in str(err.value)
