"""Self-build loop switch + 'Shut down task' for always-running tasks (offline: temp settings file,
in-memory job store, no database). One-off approvals must be left exactly as they were."""
import copy
import json
import threading
import urllib.error
import urllib.request

import pytest

import keeper_dashboard as app
from matrix import keeper_dashboard as kd
from matrix import keeper_settings
from matrix.keeper_settings import is_self_build_on
from matrix.keeper_store import MemoryStore


def one_off_jobs():
    """Job 7: waiting on a yes/no. Job 8: approved (yes) and closed by it, so it shows Change to No."""
    return [
        {"id": 7, "message": "Pending one", "tasks": [], "agents": [], "drafts": [], "created_at": None,
         "approvals": [{"type": "yes_no", "from": "BEZEL", "to": "owner", "text": "Send?", "status": "pending",
                        "at": "2026-09-27T10:00:00Z"}],
         "step_log": [{"tag": "approval asked", "approval": 0, "at": "2026-09-27T10:00:00Z"}]},
        {"id": 8, "message": "Approved one", "tasks": [], "agents": [], "drafts": [], "created_at": None,
         "approvals": [{"type": "yes_no", "from": "BEZEL", "to": "owner", "text": "Post?", "status": "yes",
                        "at": "2026-09-27T10:00:00Z", "answered_at": "2026-09-27T10:01:00Z",
                        "answered_by": "owner (dashboard)"}],
         "step_log": [{"tag": "approval asked", "approval": 0, "at": "2026-09-27T10:00:00Z"},
                      {"tag": "approval answered", "approval": 0, "at": "2026-09-27T10:02:00Z"},
                      {"tag": "job closed", "at": "2026-09-27T10:02:00Z"}]},
    ]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEPER_SETTINGS_FILE", str(tmp_path / "keeper_settings.json"))


def read_file():
    return json.loads(keeper_settings.settings_file().read_text(encoding="utf-8"))


# ---------- reader ----------
def test_missing_file_or_key_means_on(tmp_path):
    assert is_self_build_on() is True                                  # no file at all
    p = tmp_path / "other.json"
    assert is_self_build_on(settings_path=p) is True
    p.write_text(json.dumps({"worker_on": False, "changed_by": "x"}))
    assert is_self_build_on(settings_path=p) is True                   # key missing
    p.write_text(json.dumps({"self_build_on": False}))
    assert is_self_build_on(settings_path=str(p)) is False
    p.write_text(json.dumps({"self_build_on": True}))
    assert is_self_build_on(p) is True
    p.write_text("{damaged")
    assert is_self_build_on(p) is False                                # safe side
    p.write_text(json.dumps({"self_build_on": "no"}))
    assert is_self_build_on(p) is False
    assert keeper_settings.self_build_state({"worker_on": True}) is True


# ---------- writer ----------
def test_toggle_writes_bool_with_metadata_and_keeps_other_keys(monkeypatch):
    keeper_settings.set_worker_on(False, by="someone")
    worker_meta = {k: read_file()[k] for k in ("worker_on", "changed_at", "changed_by")}
    calls = []
    real = keeper_settings._write
    monkeypatch.setattr(keeper_settings, "_write", lambda d: calls.append(dict(d)) or real(d))

    data = keeper_settings.set_self_build_on(False)
    on_disk = read_file()
    assert on_disk == data and on_disk["self_build_on"] is False
    assert on_disk["self_build_changed_by"] == "owner (dashboard)" and on_disk["self_build_changed_at"].endswith("Z")
    assert on_disk["self_build_via"] == "toggle"
    assert {k: on_disk[k] for k in worker_meta} == worker_meta         # worker switch + its metadata untouched
    assert not is_self_build_on()

    keeper_settings.set_self_build_on(True)
    assert read_file()["self_build_on"] is True and is_self_build_on()
    assert len(calls) == 2                                             # both went through the one safe writer
    assert not keeper_settings.settings_file().with_suffix(".json.tmp").exists()

    keeper_settings.set_worker_on(True)                                # worker switch keeps self-build keys
    assert read_file()["self_build_on"] is True and read_file()["self_build_via"] == "toggle"


def test_toggle_refuses_to_overwrite_a_damaged_file():
    keeper_settings.settings_file().write_text("{damaged")
    with pytest.raises(OSError):
        keeper_settings.set_self_build_on(True)
    assert keeper_settings.settings_file().read_text() == "{damaged"


# ---------- pages ----------
def test_header_toggle_sits_next_to_worker_switch():
    page = kd.render_list(one_off_jobs(), settings={"worker_on": True})
    header = page.split("</header>")[0]
    assert "Keeper worker: On" in header and "Self-build loop: ON" in header
    assert header.index("Keeper worker: On") < header.index("Self-build loop: ON")
    assert "setSelfBuild(this.checked)" in header and "setWorker(this.checked)" in header
    page = kd.render_job(one_off_jobs()[1], settings={"worker_on": True, "self_build_on": False})
    assert "Self-build loop: OFF" in page.split("</header>")[0]


def test_shut_down_for_persistent_task_change_to_no_for_one_off():
    jobs = one_off_jobs()
    page = kd.render_list(jobs, settings={"worker_on": True})
    body = page.split("<script>")[0]
    assert "Always-running tasks" in body and "Self-build loop" in body
    assert "shutDownTask('self-build')" in body and "Shut down task</button>" in body
    assert "Change to No" not in body                                  # the loop card has no Change to No
    one_off = kd.render_job(jobs[1], settings={"worker_on": True}).split("<script>")[0]
    assert "changeToNo(8, 0)" in one_off and "Change to No</button>" in one_off
    assert "Shut down task" not in one_off                             # one-off items keep Change to No
    pending = kd.render_job(jobs[0], settings={"worker_on": True}).split("<script>")[0]
    assert "answer(7, 0, 'yes')" in pending and "answer(7, 0, 'no')" in pending and "setPaused(7" in pending
    off = kd.render_list(jobs, settings={"worker_on": True, "self_build_on": False, "self_build_via": "shut down task",
                                         "self_build_changed_at": "2026-09-27T10:00:00Z",
                                         "self_build_changed_by": "owner (dashboard)"}).split("<script>")[0]
    assert "Shut down task</button>" not in off and "setSelfBuild(true)" in off and "shut down" in off


def test_rendering_does_not_change_jobs():
    jobs = one_off_jobs()
    before = copy.deepcopy(jobs)
    kd.render_list(jobs, settings={"worker_on": True, "self_build_on": False})
    assert jobs == before


# ---------- server (in-memory store, temp settings, ephemeral port) ----------
@pytest.fixture
def server():
    store = MemoryStore(one_off_jobs())
    srv = app.make_server(0, app.Backend(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, store
    srv.shutdown()
    srv.server_close()


def call(srv, path, body=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if path.startswith("/api/") else text)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_flipping_and_shutdown_leave_every_job_and_approval_alone(server):
    srv, store = server
    before = copy.deepcopy(store.rows)
    code, body = call(srv, "/api/self-build", {"on": False})
    assert code == 200 and body["settings"]["self_build_on"] is False and not is_self_build_on()
    code, body = call(srv, "/api/self-build", {"on": True})
    assert code == 200 and is_self_build_on()
    assert store.rows == before
    assert "Shut down task</button>" in call(srv, "/")[1]

    code, body = call(srv, "/api/shutdown-task", {"task": "self-build"})
    assert code == 200 and not is_self_build_on()
    saved = read_file()
    assert saved["self_build_on"] is False and saved["self_build_via"] == "shut down task"
    assert saved["self_build_changed_by"] == "owner (dashboard)" and saved["self_build_changed_at"]
    assert store.rows == before                                        # no approval answered/closed/rejected
    assert store.rows[7]["approvals"][0]["status"] == "pending" and store.rows[8]["approvals"][0]["status"] == "yes"
    assert "worker_on" not in saved or saved["worker_on"] is True      # worker switch not flipped


def test_endpoints_validate_and_keep_the_origin_check(server):
    srv, store = server
    assert call(srv, "/api/self-build", {"on": "yes"})[0] == 400
    assert call(srv, "/api/shutdown-task", {"task": "something-else"})[0] == 404
    assert call(srv, "/api/self-build", {"on": False}, {"Origin": "http://evil.example"})[0] == 403
    assert call(srv, "/api/shutdown-task", {"task": "self-build"}, {"Origin": "http://evil.example"})[0] == 403
    assert is_self_build_on()                                          # the refused calls changed nothing
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/self-build",
                                 data=b'{"on": false}', headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 415 and is_self_build_on()
    keeper_settings.settings_file().write_text("{damaged")
    assert call(srv, "/api/self-build", {"on": True})[0] == 500
    assert keeper_settings.settings_file().read_text() == "{damaged"
