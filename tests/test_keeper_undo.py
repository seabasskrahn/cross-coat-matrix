"""Undo / reopen: the grace window, the dashboard's Undo and Reopen, the envelope's status rules,
and the worker's behaviour afterwards (in-memory store, fake brain, no network)."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from dashboard_client import auth

import keeper_dashboard as app
from matrix import config
from matrix import envelope as env
from matrix import keeper_dashboard as kd
from matrix import keeper_worker as kw
from matrix.keeper_store import MemoryStore


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(seconds):
    return iso(datetime.now(timezone.utc) - timedelta(seconds=seconds))


class FakeBrain:
    def __init__(self):
        self.calls = []

    def __call__(self, name, role, text):
        self.calls.append((name, text))
        return f"{name} draft #{len(self.calls)}"


def ask(status="pending", **kw):
    return {"from": "system", "to": "user", "type": "yes_no", "text": "Approve these drafts?",
            "status": status, "at": "2026-09-28T01:00:00Z", **kw}


def waiting_job(id=5, n_tasks=1):
    """A job the worker drafted and is now asking about (built by the real worker)."""
    tasks = [{"num": i + 1, "title": f"Task {i + 1}", "depends_on": [i] if i else []} for i in range(n_tasks)]
    store, brain = MemoryStore([{"id": id, "message": "Do it", "tasks": tasks,
                                 "agents": [{"name": "BEZEL", "role": "writer"}] * n_tasks,
                                 "drafts": [], "approvals": [], "step_log": []}]), FakeBrain()
    kw.sweep_once(store, brain)
    assert kd.job_status(store.rows[id]) == kd.WAITING
    return store, brain


def tags(store, id=5):
    return env.tags(store.rows[id])


@pytest.fixture(autouse=True)
def grace_30(monkeypatch):
    monkeypatch.setattr(config, "ANSWER_GRACE_SECONDS", 30.0)


# ---------- grace window (envelope + worker) ----------
def test_grace_window_blocks_the_worker_for_no_and_yes():
    for answer in ("no", "yes"):
        store, brain = waiting_job()
        kd.answer_approval(store, 5, 0, answer)           # answered just now
        assert env.in_grace(store.rows[5]) and not env.is_pending(store.rows[5])
        before = tags(store)
        kw.sweep_once(store, brain)
        assert tags(store) == before and store.claims == [5]   # left alone
        assert kd.job_status(store.rows[5]) == kd.WORKING


def test_worker_acts_once_the_window_has_passed():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    store.rows[5]["approvals"][0]["answered_at"] = ago(31)
    assert not env.in_grace(store.rows[5]) and env.is_pending(store.rows[5])
    kw.sweep_once(store, brain)
    assert tags(store)[-3:] == ["approval answered", "rejected", "job closed"]


def test_grace_window_edges_and_setting(monkeypatch):
    now = datetime(2026, 9, 28, 2, 0, 0, tzinfo=timezone.utc)
    j = {"approvals": [ask("no", answered_at=iso(now - timedelta(seconds=29)))], "step_log": []}
    assert env.in_grace(j, now) and not env.is_pending(j, now)
    assert not env.in_grace(j, now + timedelta(seconds=1)) and env.is_pending(j, now + timedelta(seconds=1))
    monkeypatch.setattr(config, "ANSWER_GRACE_SECONDS", 5.0)
    assert not env.in_grace(j, now)
    monkeypatch.setattr(config, "ANSWER_GRACE_SECONDS", 0.0)
    assert not env.in_grace({"approvals": [ask("no", answered_at=iso(now))], "step_log": []}, now)
    # no answered_at (e.g. answered in pgAdmin) = no wait, as before
    assert not env.in_grace({"approvals": [ask("no")], "step_log": []}, now)
    # an answer the worker already acted on doesn't hold anything up
    acted = {"approvals": [ask("yes", answered_at=iso(now))],
             "step_log": [{"tag": "approval answered", "approval": 0, "at": iso(now)}]}
    assert not env.in_grace(acted, now)


def test_grace_setting_reads_env(monkeypatch):
    import importlib
    monkeypatch.setenv("KEEPER_ANSWER_GRACE_SECONDS", "12")
    try:
        assert importlib.reload(config).ANSWER_GRACE_SECONDS == 12.0
        monkeypatch.delenv("KEEPER_ANSWER_GRACE_SECONDS")
        monkeypatch.setenv("KEEPER_REJECT_GRACE_SECONDS", "45")
        assert importlib.reload(config).ANSWER_GRACE_SECONDS == 45.0
    finally:
        monkeypatch.delenv("KEEPER_REJECT_GRACE_SECONDS", raising=False)
        importlib.reload(config)


# ---------- status derivation ----------
def test_reopened_after_close_is_not_closed():
    closed = [{"tag": "approval answered", "approval": 0}, {"tag": "rejected", "approval": 0}, {"tag": "job closed"}]
    j = {"approvals": [ask("no")], "step_log": list(closed)}
    assert env.is_closed(j) and kd.job_status(j) == kd.CLOSED and env.reopenable_approval(j) == 0
    j = {"approvals": [ask("pending", reopened_at="x")],
         "step_log": closed + [{"tag": "approval reopened", "approval": 0}]}
    assert not env.is_closed(j) and kd.job_status(j) == kd.WAITING and env.reopenable_approval(j) is None
    assert env.acted_approvals(j) == set() and not env.is_pending(j)
    # closed again later -> closed
    j["step_log"] += [{"tag": "approval answered", "approval": 0}, {"tag": "rejected", "approval": 0},
                      {"tag": "job closed"}]
    j["approvals"][0]["status"] = "no"
    assert env.is_closed(j) and kd.job_status(j) == kd.CLOSED


def test_row1_as_fixed_by_hand_is_waiting():
    """Job 1 after the manual fix: `job closed` removed, `approval reopened` appended."""
    row1 = {"id": 1, "message": "Build me a dashboard", "tasks": [], "agents": [], "drafts": [],
            "approvals": [{"from": "user", "to": "system", "type": "yes_no", "text": "Approve this layout?",
                           "status": "pending", "reopened_at": "2026-09-28T01:31:24Z"}],
            "step_log": [{"tag": t, "at": "2026-09-28T00:00:00Z"} for t in
                         ("message received", "task created", "agent assigned", "draft saved", "approval asked",
                          "picked up")]
            + [{"tag": "approval answered", "approval": 0, "status": "no", "at": "2026-09-28T01:30:27Z"},
               {"tag": "rejected", "approval": 0, "at": "2026-09-28T01:30:27Z"},
               {"tag": "approval reopened", "approval": 0, "note": "accidental reject undone",
                "at": "2026-09-28T01:31:24Z"}]}
    assert kd.job_status(row1) == kd.WAITING and not env.is_pending(row1)
    page = kd.render_job(row1)
    assert page.count('class="approve"') == 1 and 'class="reopen"' not in page and "Undo</button>" not in page
    store = MemoryStore([row1])
    kw.sweep_once(store, FakeBrain())
    assert store.rows[1] == row1 and store.claims == []   # the worker leaves it alone


def test_reopenable_only_after_a_rejection():
    assert env.reopenable_approval({"approvals": [ask("yes")], "step_log": [
        {"tag": "approval answered", "approval": 0}, {"tag": "job closed"}]}) is None
    assert env.reopenable_approval({"approvals": [], "step_log": [
        {"tag": "job closed", "detail": "no approval needed (internal only)"}]}) is None
    # main-flow style: `rejected` without an index -> the last answered approval
    assert env.reopenable_approval({"approvals": [ask("yes"), ask("no")], "step_log": [
        {"tag": "approval answered", "approval": 1, "status": "no"}, {"tag": "rejected", "detail": "cancelled"},
        {"tag": "job closed"}]}) == 1


# ---------- Undo ----------
def test_undo_within_the_window_puts_it_back():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    item = kd.reopen_approval(store, 5, 0, kd.UNDO)
    a = store.rows[5]["approvals"][0]
    assert a == item and a["status"] == "pending" and "reopened_at" in a
    assert not {"answered_at", "answered_by", "answer"} & set(a)
    last = store.rows[5]["step_log"][-1]
    assert last["tag"] == "approval reopened" and last["approval"] == 0 and last["via"] == "undo"
    assert kd.job_status(store.rows[5]) == kd.WAITING
    a["answered_at"] = ago(120)     # even much later: still waiting, the worker leaves it
    kw.sweep_once(store, brain)
    assert tags(store)[-1] == "approval reopened"


def test_undo_a_written_answer_removes_it():
    store = MemoryStore([{"id": 7, "message": "m", "approvals": [
        {"type": "context_request", "text": "Which crew?", "status": "pending"}], "step_log": []}])
    kd.answer_approval(store, 7, 0, "answer", "Crew B")
    kd.reopen_approval(store, 7, 0, kd.UNDO)
    assert "answer" not in store.rows[7]["approvals"][0] and env.has_pending_approval(store.rows[7])


def test_undo_after_the_worker_acted_is_refused():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    with pytest.raises(kd.AnswerError) as err:
        kd.reopen_approval(store, 5, 0, kd.UNDO)
    assert err.value.code == 409 and "Reopen" in str(err.value)
    with pytest.raises(kd.AnswerError):
        kd.plan_reopen(store.rows[5], 3, kd.UNDO)             # no such approval


def test_undo_refused_while_the_worker_holds_the_job():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "yes")
    store.lock(5)
    with pytest.raises(kd.AnswerError) as err:
        kd.reopen_approval(store, 5, 0, kd.UNDO)
    assert "working on this job" in str(err.value)
    assert store.rows[5]["approvals"][0]["status"] == "yes"


def test_undo_on_a_pending_approval_is_refused():
    store, _ = waiting_job()
    with pytest.raises(kd.AnswerError) as err:
        kd.reopen_approval(store, 5, 0, kd.UNDO)
    assert err.value.code == 409


# ---------- Reopen after close, then the worker carries on ----------
def test_reopen_after_close_then_yes_drafts_remaining_tasks():
    store, brain = waiting_job(n_tasks=3)
    store.rows[5]["drafts"] = store.rows[5]["drafts"][:1]  # pretend only task 1 was drafted so far
    kd.answer_approval(store, 5, 0, "no")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert kd.job_status(store.rows[5]) == kd.CLOSED
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, 1, kd.REOPEN)             # wrong approval
    kd.reopen_approval(store, 5, None, kd.REOPEN)
    j = store.rows[5]
    assert j["approvals"][0]["status"] == "pending" and j["step_log"][-1]["via"] == "reopen"
    assert kd.job_status(j) == kd.WAITING and not env.is_pending(j)
    calls = len(brain.calls)
    kw.sweep_once(store, brain)                                   # waits on the approval
    assert len(brain.calls) == calls and tags(store)[-1] == "approval reopened"
    kd.answer_approval(store, 5, 0, "yes")
    kw.sweep_once(store, brain)                                   # still inside the grace window
    assert tags(store)[-1] == "approval reopened"
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    t = tags(store)
    k = len(t) - 1 - t[::-1].index("approval reopened")
    after = t[k + 1:]
    assert after[:2] == ["picked up", "approval answered"] and "rejected" not in after
    assert after.count("draft saved") == 2 and after[-1] == "approval asked"   # tasks 2 and 3, then asks again
    assert sorted(n for d in store.rows[5]["drafts"] for n in env.draft_task_nums(d)) == [1, 2, 3]
    assert kd.job_status(store.rows[5]) == kd.WAITING


def test_reopen_after_close_then_yes_with_nothing_left_closes():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    kd.reopen_approval(store, 5, None, kd.REOPEN)
    kd.answer_approval(store, 5, 0, "yes")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert tags(store)[-2:] == ["approval answered", "job closed"] and kd.job_status(store.rows[5]) == kd.CLOSED
    assert len(brain.calls) == 1


def test_reopen_refused_unless_closed_by_reject():
    store, brain = waiting_job()
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, None, kd.REOPEN)
    kd.answer_approval(store, 5, 0, "yes")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert kd.job_status(store.rows[5]) == kd.CLOSED
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, None, kd.REOPEN)


# ---------- Postgres path: row lock + worker lock check, one transaction ----------
class FakeConn:
    def __init__(self, row, worker_busy=False):
        self.row, self.busy, self.sql, self.in_tx = row, worker_busy, [], False

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
                if "advisory" in text:
                    return {"ok": not conn.busy}
                return conn.row
        if text.startswith("UPDATE"):
            idx, item, steps = int(params[0]), params[1].obj, params[2].obj
            self.row["approvals"][idx] = item
            self.row["step_log"] += steps
        return Cur()


def fake_pg(row, busy=False):
    pytest.importorskip("psycopg")
    from psycopg import sql
    store = type("FakePg", (), {})()
    store.conn, store._mutex, store.table = FakeConn(row, busy), threading.RLock(), sql.Identifier("public", "jobs")
    return store


def test_undo_postgres_path_is_one_locked_transaction():
    row = {"id": 5, "approvals": [ask("no", answered_at=ago(3), answered_by="owner (dashboard)")], "step_log": []}
    store = fake_pg(row)
    kd.reopen_approval(store, 5, 0, kd.UNDO)
    (sel, t1), (lock, t2), (upd, t3) = store.conn.sql
    assert "FOR UPDATE" in sel and "pg_try_advisory_xact_lock" in lock and t1 and t2 and t3
    assert "approvals = jsonb_set" in upd and "step_log = COALESCE" in upd
    assert row["approvals"][0]["status"] == "pending" and row["step_log"][-1]["tag"] == "approval reopened"


def test_undo_postgres_path_refused_while_worker_busy():
    row = {"id": 5, "approvals": [ask("no", answered_at=ago(3))], "step_log": []}
    store = fake_pg(row, busy=True)
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, 0, kd.UNDO)
    assert not any(q.startswith("UPDATE") for q, _ in store.conn.sql)


# ---------- dashboard pages ----------
def test_page_shows_undo_countdown_then_plain_undo_then_reopen():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    page = kd.render_job(store.rows[5])
    assert "Undo</button>" in page and "data-deadline=" in page and "s left to undo" in page
    assert 'class="approve"' not in page and "undo open" in kd.render_list(store.candidates())
    store.rows[5]["approvals"][0]["answered_at"] = ago(35)
    page = kd.render_job(store.rows[5])
    assert "Undo</button>" in page and "data-deadline=" not in page and "hasn't acted" in page
    kw.sweep_once(store, brain)
    page = kd.render_job(store.rows[5])
    assert "Undo</button>" not in page and page.count("reopenJob(5)") == 2 and "confirm(" in page
    assert page.count("changeToYes(5, 0)") == 2


def test_undo_after_yes_offers_no_change_to_yes():
    store, _ = waiting_job()
    kd.answer_approval(store, 5, 0, "yes")
    page = kd.render_job(store.rows[5])
    assert "Undo</button>" in page and "changeToYes(5" not in page


def test_reject_within_window_shows_undo_and_change_to_yes():
    store, _ = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    page = kd.render_job(store.rows[5])
    assert "Undo</button>" in page and "changeToYes(5, 0)" in page and "Change your answer to YES?" in page


# ---------- Change to Yes ----------
def test_change_to_yes_within_window():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    item = kd.reopen_approval(store, 5, 0, kd.CHANGE_TO_YES)
    a = store.rows[5]["approvals"][0]
    assert a == item and a["status"] == "yes" and a["changed_from"] == "no" and "reopened_at" in a
    assert a["answered_by"] == kd.ANSWERED_BY and env.parse_utc(a["answered_at"]) is not None
    assert store.rows[5]["step_log"][-1]["tag"] == "approval reopened"
    assert store.rows[5]["step_log"][-1]["via"] == "change to yes"
    assert env.in_grace(store.rows[5])          # a fresh grace window (Undo still possible)
    kw.sweep_once(store, brain)
    assert tags(store)[-1] == "approval reopened"
    a["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert tags(store)[-2:] == ["approval answered", "job closed"] and "rejected" not in tags(store)
    assert store.rows[5]["step_log"][-2]["status"] == "yes"


def test_change_to_yes_after_close_drafts_remaining_tasks():
    store, brain = waiting_job(n_tasks=3)
    store.rows[5]["drafts"] = store.rows[5]["drafts"][:1]
    kd.answer_approval(store, 5, 0, "no")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert kd.job_status(store.rows[5]) == kd.CLOSED
    before = list(store.rows[5]["step_log"])
    kd.reopen_approval(store, 5, None, kd.CHANGE_TO_YES)
    assert store.rows[5]["step_log"][:len(before)] == before       # append-only
    assert kd.job_status(store.rows[5]) == kd.WORKING and not env.is_closed(store.rows[5])
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    after = tags(store)[len(before) + 1:]
    assert after[:2] == ["picked up", "approval answered"] and after.count("draft saved") == 2
    assert after[-1] == "approval asked" and kd.job_status(store.rows[5]) == kd.WAITING


def test_change_to_yes_refused_cases():
    store, brain = waiting_job()
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, 0, kd.CHANGE_TO_YES)           # still pending
    kd.answer_approval(store, 5, 0, "yes")
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, 0, kd.CHANGE_TO_YES)           # already yes
    ctx = MemoryStore([{"id": 7, "message": "m", "step_log": [], "approvals": [
        {"type": "context_request", "text": "Which crew?", "status": "pending"}]}])
    kd.answer_approval(ctx, 7, 0, "answer", "Crew B")
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(ctx, 7, 0, kd.CHANGE_TO_YES)             # a written answer


@pytest.fixture
def server():
    store, brain = waiting_job()
    srv = app.make_server(0, app.Backend(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, store, brain
    srv.shutdown()
    srv.server_close()


def post(srv, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **auth(srv)})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_reject_undo_reopen(server):
    srv, store, brain = server
    assert post(srv, "/api/answer", {"job": 5, "index": 0, "action": "no"})[0] == 200
    code, body = post(srv, "/api/undo", {"job": 5, "index": 0})
    assert code == 200 and body["approval"]["status"] == "pending"
    assert post(srv, "/api/undo", {"job": 5, "index": 0})[0] == 409
    assert post(srv, "/api/reopen", {"job": 5})[0] == 409          # not closed
    post(srv, "/api/answer", {"job": 5, "index": 0, "action": "no"})
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert post(srv, "/api/undo", {"job": 5, "index": 0})[0] == 409
    assert post(srv, "/api/reopen", {"job": "5"})[0] == 400
    code, body = post(srv, "/api/reopen", {"job": 5})
    assert code == 200 and kd.job_status(store.rows[5]) == kd.WAITING


def test_server_change_to_yes(server):
    srv, store, brain = server
    post(srv, "/api/answer", {"job": 5, "index": 0, "action": "no"})
    code, body = post(srv, "/api/change-to-yes", {"job": 5, "index": 0})
    assert code == 200 and body["approval"]["status"] == "yes"
    assert post(srv, "/api/change-to-yes", {"job": 5, "index": 0})[0] == 409
