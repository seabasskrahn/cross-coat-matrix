"""Change to No / Yes symmetry, per-job pause, and the global worker On/Off switch (offline)."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

import keeper_dashboard as app
from matrix import config
from matrix import envelope as env
from matrix import keeper_dashboard as kd
from matrix import keeper_settings
from matrix import keeper_worker as kw
from matrix.keeper_store import MemoryStore


def ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeBrain:
    def __init__(self):
        self.calls = []

    def __call__(self, name, role, text):
        self.calls.append(name)
        return f"{name} draft #{len(self.calls)}"


def waiting_job(id=5):
    store, brain = MemoryStore([{"id": id, "message": "Do it", "tasks": [{"num": 1, "title": "T1", "depends_on": []}],
                                 "agents": [{"name": "BEZEL", "role": "writer"}], "drafts": [], "approvals": [],
                                 "step_log": []}]), FakeBrain()
    kw.sweep_once(store, brain)
    return store, brain


def answer_and_let_worker_act(store, brain, answer, id=5):
    kd.answer_approval(store, id, 0, answer)
    store.rows[id]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)


def tags(store, id=5):
    return env.tags(store.rows[id])


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ANSWER_GRACE_SECONDS", 30.0)
    monkeypatch.setenv("KEEPER_SETTINGS_FILE", str(tmp_path / "keeper_settings.json"))


# ---------- symmetric Undo / Change ----------
def test_undo_works_after_approve_too():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "yes")
    kd.reopen_approval(store, 5, 0, kd.UNDO)
    assert store.rows[5]["approvals"][0]["status"] == "pending" and kd.job_status(store.rows[5]) == kd.WAITING


def test_change_to_no_within_window_after_approve():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "yes")
    page = kd.render_job(store.rows[5])
    assert "Undo</button>" in page and "changeToNo(5, 0)" in page and "changeToYes(5" not in page
    kd.reopen_approval(store, 5, 0, kd.CHANGE_TO_NO)
    a = store.rows[5]["approvals"][0]
    assert a["status"] == "no" and a["changed_from"] == "yes" and env.in_grace(store.rows[5])
    a["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert tags(store)[-3:] == ["approval answered", "rejected", "job closed"]


def test_change_to_no_after_approved_close_marks_rejected_and_keeps_drafts():
    store, brain = waiting_job()
    answer_and_let_worker_act(store, brain, "yes")
    assert kd.job_status(store.rows[5]) == kd.CLOSED and env.closing_answer(store.rows[5]) == (0, "yes")
    page = kd.render_job(store.rows[5])
    assert page.count("changeToNo(5, 0)") == 2 and "reopenJob(5)" not in page and "changeToYes(5" not in page
    assert "Drafts already made stay as they are" in page and "confirm(" in page
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, None, kd.CHANGE_TO_YES)          # it's already yes
    with pytest.raises(kd.AnswerError):
        kd.reopen_approval(store, 5, None, kd.REOPEN)                 # Reopen is for rejections
    drafts = list(store.rows[5]["drafts"])
    before = list(store.rows[5]["step_log"])
    kd.reopen_approval(store, 5, None, kd.CHANGE_TO_NO)
    assert store.rows[5]["step_log"][:len(before)] == before          # append-only
    assert store.rows[5]["step_log"][-1]["via"] == "change to no"
    assert kd.job_status(store.rows[5]) == kd.WORKING                  # reopened, inside the grace window
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    kw.sweep_once(store, brain)
    assert tags(store)[-3:] == ["approval answered", "rejected", "job closed"]
    assert store.rows[5]["drafts"] == drafts and len(brain.calls) == 1
    # and back again: now it can be changed to Yes (or reopened)
    assert env.closing_answer(store.rows[5]) == (0, "no")
    assert kd.can_change(store.rows[5], "yes") == [0] and kd.can_change(store.rows[5], "no") == []


def test_closing_answer_cases():
    assert env.closing_answer({"approvals": [], "step_log": [{"tag": "job closed"}]}) is None
    assert env.closing_answer({"approvals": [{"type": "yes_no", "status": "yes"}], "step_log": [
        {"tag": "approval answered", "approval": 0}, {"tag": "not wired up"}, {"tag": "job closed"}]}) == (0, "yes")
    assert env.closing_answer({"approvals": [{"type": "yes_no", "status": "yes"}], "step_log": [
        {"tag": "approval answered", "approval": 0}]}) is None                       # not closed
    assert env.closing_answer({"approvals": [{"type": "yes_no", "status": "no"}], "step_log": [
        {"tag": "approval answered", "approval": 0}, {"tag": "rejected", "approval": 0},
        {"tag": "job closed"}]}) == (0, "no")


# ---------- per-job pause ----------
def test_pause_makes_the_worker_skip_the_job():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    assert kd.set_paused(store, 5, True) is True
    assert tags(store)[-1] == "paused" and env.is_paused(store.rows[5]) and not env.is_pending(store.rows[5])
    assert kd.job_status(store.rows[5]) == kd.PAUSED
    kd.set_paused(store, 5, True)                                     # no-op when already paused
    assert tags(store).count("paused") == 1
    kw.sweep_once(store, brain)
    assert tags(store)[-1] == "paused" and store.claims == [5]
    page = kd.render_job(store.rows[5])
    assert "This job is paused" in page and "setPaused(5, !this.checked)" in page
    kd.set_paused(store, 5, False)
    assert tags(store)[-1] == "resumed" and env.is_pending(store.rows[5])
    kw.sweep_once(store, brain)
    assert tags(store)[-3:] == ["approval answered", "rejected", "job closed"]
    with pytest.raises(kd.AnswerError):
        kd.set_paused(store, 5, True)                                 # closed: nothing to pause
    assert "setPaused(" not in kd.render_job(store.rows[5]).split("<script>")[0]


def test_pause_resume_does_not_clear_a_halt():
    j = {"approvals": [], "step_log": [{"tag": "circuit breaker"}, {"tag": "paused"}, {"tag": "resumed"}]}
    assert env.is_halted(j) and not env.is_pending(j) and kd.job_status(j) == kd.HALTED
    j["step_log"].append({"tag": "resume"})
    assert not env.is_halted(j)


def test_paused_waiting_job_can_still_be_answered():
    store, brain = waiting_job()
    kd.set_paused(store, 5, True)
    page = kd.render_job(store.rows[5])
    assert 'class="approve"' in page and kd.job_status(store.rows[5]) == kd.PAUSED
    assert kd.summary(store.candidates())[kd.PAUSED] == 1


# ---------- global worker On/Off ----------
def test_worker_switch_file(tmp_path):
    assert keeper_settings.load() == {"worker_on": True} and keeper_settings.worker_on()
    data = keeper_settings.set_worker_on(False)
    assert data["worker_on"] is False and data["changed_by"] == "owner (dashboard)"
    assert json.loads(keeper_settings.settings_file().read_text())["worker_on"] is False
    assert not keeper_settings.worker_on()
    keeper_settings.set_worker_on(True)
    assert keeper_settings.worker_on()
    keeper_settings.settings_file().write_text("{not json")
    s = keeper_settings.load()
    assert s["worker_on"] is False and "unreadable" in s["problem"]


def test_worker_off_claims_nothing_then_on_again():
    store, brain = waiting_job()
    kd.answer_approval(store, 5, 0, "no")
    store.rows[5]["approvals"][0]["answered_at"] = ago(40)
    keeper_settings.set_worker_on(False)
    result = kw.sweep_once(store, brain)
    assert result.off and result.processed == [] and store.claims == [5]
    assert tags(store)[-1] == "approval asked"
    keeper_settings.set_worker_on(True)
    result = kw.sweep_once(store, brain)
    assert not result.off and tags(store)[-1] == "job closed"


def test_pages_show_the_switch_and_off_banner():
    store, _ = waiting_job()
    page = kd.render_list(store.candidates(), settings={"worker_on": True})
    assert "setWorker(this.checked)" in page and "Keeper worker: On" in page and "is OFF" not in page
    page = kd.render_list(store.candidates(), settings={"worker_on": False})
    assert "Keeper worker: Off" in page and "Keeper worker is OFF" in page
    page = kd.render_job(store.rows[5], settings={"worker_on": False, "problem": "file unreadable"})
    assert "file unreadable" in page


# ---------- server ----------
@pytest.fixture
def server():
    store, brain = waiting_job()
    srv = app.make_server(0, app.Backend(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, store, brain
    srv.shutdown()
    srv.server_close()


def call(srv, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if body is not None or path.startswith("/api/") else text)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_worker_switch(server):
    srv, store, brain = server
    code, body = call(srv, "/api/worker", {"on": False})
    assert code == 200 and not keeper_settings.worker_on()
    assert "Keeper worker is OFF" in call(srv, "/")[1]
    assert call(srv, "/api/jobs")[1]["settings"]["worker_on"] is False
    assert call(srv, "/api/worker", {"on": "yes"})[0] == 400
    call(srv, "/api/worker", {"on": True})
    assert keeper_settings.worker_on()


def test_server_pause_and_change_to_no(server):
    srv, store, brain = server
    assert call(srv, "/api/pause", {"job": 5, "paused": True}) == (200, {"ok": True, "paused": True})
    assert kd.job_status(store.rows[5]) == kd.PAUSED
    assert call(srv, "/api/pause", {"job": 5, "paused": "no"})[0] == 400
    assert call(srv, "/api/pause", {"job": 99, "paused": True})[0] == 404
    call(srv, "/api/pause", {"job": 5, "paused": False})
    call(srv, "/api/answer", {"job": 5, "index": 0, "action": "yes"})
    code, body = call(srv, "/api/change-to-no", {"job": 5, "index": 0})
    assert code == 200 and body["approval"]["status"] == "no"
    assert call(srv, "/api/change-to-no", {"job": 5, "index": 0})[0] == 409
