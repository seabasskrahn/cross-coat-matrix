"""Dashboard MAIN BRAIN switch: rewrites only LLM_PROVIDER in a TEMP .env, backup first, no secrets
shown. Never touches the real .env (conftest points KEEPER_ENV_FILE at a temp path too)."""
import json
import threading
import urllib.error
import urllib.request

import pytest
from dashboard_client import auth

import keeper_dashboard as app
from matrix import brain_switch as bs
from matrix import keeper_dashboard as kd
from matrix.keeper_store import MemoryStore

SECRETS = ["xai-SECRET-123", "AIza-SECRET-456", "tvly-SECRET-789", "pw-SECRET-000"]
ENV = ("# ===== MAIN BRAIN: change to gemini or xai =====\r\n"
       "# comment line stays\r\n"
       "LLM_PROVIDER=xai\r\n"
       "\r\n"
       f"GOOGLE_API_KEY={SECRETS[1]}\r\n"
       "GEMINI_MODEL=gemini-3.1-pro-preview\r\n"
       f"XAI_API_KEY={SECRETS[0]}\r\n"
       "XAI_MODEL=grok-4.6\r\n"
       f"TAVILY_API_KEY={SECRETS[2]}\r\n"
       f"CHECKPOINT_DB_URL=postgresql://matrix:{SECRETS[3]}@localhost:5432/keeper\r\n").encode()
LOG = ("2026-09-27 20:03:56,939 INFO keeper_worker brains: main=gemini; SUNDAY=gemini:gemini-3.1-pro-preview\n"
       "2026-09-27 20:24:09,287 INFO matrix.config main brain changed: gemini -> xai\n"
       "2026-09-27 20:24:09,288 INFO keeper_worker brains: main=xai; SUNDAY=xai:grok-4.6, STEWARD=xai:grok-4.6\n")


@pytest.fixture
def files(tmp_path, monkeypatch):
    envf, logs = tmp_path / ".env", tmp_path / "logs"
    logs.mkdir()
    envf.write_bytes(ENV)
    (logs / "keeper_worker.log").write_text(LOG, encoding="utf-8")
    monkeypatch.setenv("KEEPER_ENV_FILE", str(envf))
    monkeypatch.setenv("KEEPER_BACKUP_DIR", str(logs))
    monkeypatch.setenv("KEEPER_WORKER_LOG", str(logs / "keeper_worker.log"))
    for k in ("XAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return envf, logs


def backups(logs):
    return sorted(p for p in logs.iterdir() if p.name.startswith(".env.bak-"))


def test_rewrites_only_the_provider_line(files):
    envf, logs = files
    result = bs.set_main_brain("gemini")
    assert result["previous"] == "xai" and result["now"] == "gemini"
    assert envf.read_bytes() == ENV.replace(b"LLM_PROVIDER=xai\r\n", b"LLM_PROVIDER=gemini\r\n")
    assert bs.current() == "gemini"
    bs.set_main_brain("XAI ")
    assert envf.read_bytes() == ENV                                   # byte-for-byte back to the original
    assert [p.name for p in envf.parent.iterdir() if ".tmp-" in p.name] == []  # no temp file left


def test_lf_file_and_spacing_are_kept(files):
    envf, _ = files
    envf.write_bytes(b"A=1\nLLM_PROVIDER = gemini\nB=2")
    bs.set_main_brain("xai")
    assert envf.read_bytes() == b"A=1\nLLM_PROVIDER = xai\nB=2"


def test_backup_is_created_first(files):
    envf, logs = files
    bs.set_main_brain("gemini")
    bs.set_main_brain("xai")
    made = backups(logs)
    assert len(made) == 2 and made[0].read_bytes() == ENV
    assert made[1].read_bytes() == ENV.replace(b"=xai\r\n", b"=gemini\r\n", 1)


@pytest.mark.parametrize("bad", ["mock", "claude", "", "grok", "xai\nEVIL=1", None, 1, True, ["xai"]])
def test_invalid_values_rejected(files, bad):
    envf, logs = files
    with pytest.raises(bs.BrainSwitchError):
        bs.set_main_brain(bad)
    assert envf.read_bytes() == ENV and backups(logs) == []


def test_missing_or_duplicate_line_refused(files):
    envf, logs = files
    envf.write_bytes(b"A=1\r\n")
    with pytest.raises(bs.BrainSwitchError):
        bs.set_main_brain("xai")
    envf.write_bytes(b"LLM_PROVIDER=xai\r\nLLM_PROVIDER=gemini\r\n")
    with pytest.raises(bs.BrainSwitchError):
        bs.set_main_brain("xai")
    assert envf.read_bytes() == b"LLM_PROVIDER=xai\r\nLLM_PROVIDER=gemini\r\n" and backups(logs) == []


def test_resolved_brains_and_pins(files):
    info = bs.panel_info()
    got = {a["agent"]: a for a in info["agents"]}
    assert info["main"] == "xai"
    assert got["STEWARD"]["brain"] == "xai:grok-4.6" and got["STEWARD"]["pinned"]
    assert got["BEZEL"]["brain"] == "gemini:gemini-3.1-pro-preview" and got["BEZEL"]["pinned"]
    assert got["TAPER"]["brain"] == "xai:grok-4.6" and not got["TAPER"]["pinned"]
    bs.set_main_brain("gemini")
    got = {a["agent"]: a for a in bs.panel_info()["agents"]}
    assert got["TAPER"]["brain"] == "gemini:gemini-3.1-pro-preview" and got["STEWARD"]["brain"] == "xai:grok-4.6"


def test_worker_reported_reads_latest_brains_line(files):
    r = bs.worker_reported()
    assert r["at"] == "2026-09-27 20:24:09" and r["main"] == "xai"


def test_html_shows_switch_and_no_secrets(files):
    store = MemoryStore([{"id": 1, "message": "m", "tasks": [], "agents": [], "drafts": [], "approvals": [],
                          "step_log": []}])
    info = bs.panel_info()
    for page in (kd.render_list(store.candidates(), settings={"worker_on": True}, brain=info),
                 kd.render_job(store.rows[1], settings={"worker_on": True}, brain=info)):
        assert "Main brain: <b>xAI (Grok)</b>" in page and "setBrain(" in page
        assert "(pinned)" in page and "Keeper worker last reported (8:24 PM)" in page
        assert 'class="sel" onclick="setBrain(\'xai\'' in page
        for s in SECRETS:
            assert s not in page
        assert "CHECKPOINT_DB_URL" not in page and "API_KEY" not in page


def test_no_env_file_shows_a_safe_message(files):
    files[0].unlink()
    page = kd.render_list([], settings={"worker_on": True}, brain=bs.panel_info())
    assert "Cannot read .env right now." in page


# ---------- server ----------
@pytest.fixture
def server(files):
    store = MemoryStore([])
    srv = app.make_server(0, app.Backend(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(auth(srv) if body is not None else {}), **(headers or {})}
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}",
                                 data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if body is not None else text)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_server_switch_endpoint(server, files):
    envf, logs = files
    code, body = call(server, "/api/brain", {"provider": "gemini"})
    assert code == 200 and body == {"ok": True, "main": "gemini", "previous": "xai"}
    assert bs.current() == "gemini" and len(backups(logs)) == 1
    page = call(server, "/")[1]
    assert "Main brain: <b>Gemini</b>" in page and not any(s in page for s in SECRETS)
    assert call(server, "/api/brain", {"provider": "mock"})[0] == 400
    assert call(server, "/api/brain", {})[0] == 400
    assert bs.current() == "gemini"


def test_server_switch_has_local_only_protections(server, files):
    envf, _ = files
    port = server.server_address[1]
    assert call(server, "/api/brain", {"provider": "gemini"}, {"Origin": "http://evil.example"})[0] == 403
    assert call(server, "/api/brain", {"provider": "gemini"}, {"Host": "evil.example"})[0] == 403
    assert call(server, "/api/brain", {"provider": "gemini"}, {"Content-Type": "text/plain"})[0] == 415
    assert envf.read_bytes() == ENV
    assert call(server, "/api/brain", {"provider": "gemini"}, {"Origin": f"http://127.0.0.1:{port}"})[0] == 200


# ---------- BEZEL toggle ----------
def test_bezel_toggle_adds_line_under_main_when_missing(files):
    envf, logs = files
    assert bs.current_bezel() == ("gemini", "config pin")           # absent = the config pin, shown honestly
    result = bs.set_bezel_brain("xai")
    assert result == {"previous": "", "now": "xai", "backup": backups(logs)[0].name}
    assert envf.read_bytes() == ENV.replace(b"LLM_PROVIDER=xai\r\n", b"LLM_PROVIDER=xai\r\nBRAIN_BEZEL=xai\r\n")
    assert bs.current_bezel() == ("xai", ".env")
    got = {a["agent"]: a for a in bs.panel_info()["agents"]}
    assert got["BEZEL"]["brain"] == "xai:grok-4.6" and got["BEZEL"]["pinned"]


def test_bezel_toggle_rewrites_only_its_line(files):
    envf, logs = files
    with_line = ENV.replace(b"LLM_PROVIDER=xai\r\n", b"LLM_PROVIDER=xai\r\n# BEZEL override\r\nBRAIN_BEZEL=xai\r\n")
    envf.write_bytes(with_line)
    bs.set_bezel_brain("gemini")
    assert envf.read_bytes() == with_line.replace(b"BRAIN_BEZEL=xai", b"BRAIN_BEZEL=gemini")
    assert bs.current() == "xai" and bs.current_bezel() == ("gemini", ".env")
    assert backups(logs)[0].read_bytes() == with_line


def test_bezel_line_added_at_end_without_main_line(files):
    envf, _ = files
    envf.write_bytes(b"A=1")
    bs.set_bezel_brain("gemini")
    assert envf.read_bytes() == b"A=1\nBRAIN_BEZEL=gemini\n"


@pytest.mark.parametrize("bad", ["mock", "claude", "", "xai:grok-9", None, 5])
def test_bezel_invalid_values_rejected(files, bad):
    envf, logs = files
    with pytest.raises(bs.BrainSwitchError):
        bs.set_bezel_brain(bad)
    assert envf.read_bytes() == ENV and backups(logs) == []


def test_bezel_toggle_on_page_and_server(server, files):
    envf, logs = files
    page = call(server, "/")[1]
    assert "BEZEL&#39;s brain: <b>Gemini</b>" in page and "config pin" in page and "setBezel(" in page
    code, body = call(server, "/api/bezel-brain", {"provider": "xai"})
    assert code == 200 and body == {"ok": True, "bezel": "xai", "previous": ""}
    page = call(server, "/")[1]
    assert "BEZEL&#39;s brain: <b>Grok</b>" in page and "config pin" not in page
    assert "Main brain: <b>xAI (Grok)</b>" in page and not any(s in page for s in SECRETS)
    assert call(server, "/api/bezel-brain", {"provider": "mock"})[0] == 400
    assert call(server, "/api/bezel-brain", {"provider": "xai"}, {"Origin": "http://evil.example"})[0] == 403
    assert bs.current() == "xai"                                     # main line untouched


def test_stray_carriage_return_is_read_and_kept(files):
    envf, _ = files
    odd = ENV.replace(b"LLM_PROVIDER=xai\r\n", b"LLM_PROVIDER=xai\r\r\n")   # what a hand edit left behind
    envf.write_bytes(odd)
    assert bs.current() == "xai"
    bs.set_main_brain("gemini")
    assert envf.read_bytes() == odd.replace(b"=xai\r\r\n", b"=gemini\r\r\n", 1)
