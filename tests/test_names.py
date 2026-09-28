"""Rename feature: names file load/save/validation/defaults, chat output, and the panel server."""
import json
import os
import threading
import urllib.error
import urllib.request

os.environ["LLM_PROVIDER"] = "mock"
os.environ["CHECKPOINT_DB_URL"] = ""

import pytest  # noqa: E402

from matrix import agents, names  # noqa: E402


@pytest.fixture(autouse=True)
def names_file(tmp_path, monkeypatch):
    path = tmp_path / "matrix_names.json"
    monkeypatch.setenv("MATRIX_NAMES_FILE", str(path))
    monkeypatch.delenv("SHOW_ROUTING", raising=False)
    return path


def rename(data, entry_id, **fields):
    for entry in data["agents"] + data["services"]:
        if entry["id"] == entry_id:
            entry.update(fields)
    return data


def test_missing_file_gives_defaults(names_file):
    assert not names_file.exists()
    assert names.assistant_name() == "Stuart"
    assert names.display_name("SUNDAY") == "SUNDAY"
    assert names.load() == names.defaults()


def test_defaults_cover_every_agent_and_service():
    data = names.defaults()
    ids = [e["id"] for e in data["agents"]]
    assert ids[:2] == ["ASSISTANT", "SUNDAY"]
    assert set(agents.SENIOR_STAFF) | set(agents.SPECIALISTS) <= set(ids)
    services = {e["id"]: e["status"] for e in data["services"]}
    for sid in ("GROK", "GEMINI", "GITHUB", "TAVILY", "FILE_TOOL"):
        assert services[sid] == "connected"
    for sid in ("CLAUDE", "N8N", "POSTGRES"):
        assert services[sid] == "off"
    for sid in ("TELEGRAM", "GOOGLE", "QUICKBOOKS"):
        assert services[sid] == "planned"
    assert "LUMEN" not in ids


def test_defaults_are_the_locked_baseline():
    data = names.defaults()
    assert [(e["id"], e["display"]) for e in data["agents"]] == [("ASSISTANT", "Stuart")] + [
        (n, n) for n in ["SUNDAY", "STEWARD", "BEZEL", "MARGIN", "VECTOR", "FINISH",
                         "TAPER", "ARMOR", "DEDUCT", "AUDIT", "LEDGER"]]
    assert [e["display"] for e in data["services"]][:8] == [
        "Grok", "Gemini", "Claude", "GitHub", "Tavily", "File tool", "n8n", "Postgres"]


def test_new_agent_appears_automatically(monkeypatch):
    monkeypatch.setitem(agents.SPECIALISTS, "FORGE", ("builds the Matrix", ["build"]))
    forge = [e for e in names.load()["agents"] if e["id"] == "FORGE"][0]
    assert forge["display"] == "FORGE" and forge["description"] == "builds the Matrix"


def test_save_and_load_round_trip(names_file):
    data = rename(names.defaults(), "ASSISTANT", display="  Jarvis ")
    rename(data, "TAPER", display="Crew Boss", color="#123456", icon="🦺", description="site stuff")
    names.save(data)
    saved = json.loads(names_file.read_text(encoding="utf-8"))
    assert saved["agents"][0]["display"] == "Jarvis"
    assert names.assistant_name() == "Jarvis"
    assert names.display_name("TAPER") == "Crew Boss"
    assert names.pretty("9:30 AM BEZEL: delegated to TAPER") == "9:30 AM BEZEL: delegated to Crew Boss"


@pytest.mark.parametrize("fields, problem", [
    ({"display": ""}, "empty"),
    ({"display": "   "}, "empty"),
    ({"display": "x" * 41}, "too long"),
    ({"display": "BEZEL"}, "used twice"),
    ({"display": "bezel"}, "used twice"),
    ({"color": "red"}, "#RRGGBB"),
])
def test_validation_rejects_bad_names(names_file, fields, problem):
    with pytest.raises(names.NamesError) as err:
        names.save(rename(names.defaults(), "TAPER", **fields))
    assert problem in str(err.value)
    assert not names_file.exists()  # nothing written


def test_broken_file_falls_back_to_defaults(names_file):
    names_file.write_text("{not json", encoding="utf-8")
    assert names.assistant_name() == "Stuart"


def test_reset(names_file):
    names.save(rename(names.defaults(), "ASSISTANT", display="Jarvis"))
    names.reset()
    assert names.assistant_name() == "Stuart"


def test_chat_uses_custom_name(monkeypatch, capsys):
    import chat
    names.save(rename(names.defaults(), "ASSISTANT", display="Jarvis"))
    rename_data = rename(names.load(), "TAPER", display="Crew Boss")
    names.save(rename_data)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "quit")
    chat.main()
    out = capsys.readouterr().out
    assert "Jarvis is here. Type 'quit' to stop." in out and "Jarvis: Goodbye" in out
    assert "Stuart" not in out
    assert chat.clean("[TAPER mock] done by SUNDAY") == "Jarvis done by Jarvis"
    monkeypatch.setenv("SHOW_ROUTING", "1")
    chat.show({"step_log": [{"tag": "agent assigned", "at": "2026-09-28T13:00:00Z", "agent": "TAPER",
                             "role": "field ops", "via": "BEZEL"}], "reply": "ok", "status": "done"})
    out = capsys.readouterr().out
    assert "agent assigned: Crew Boss (field ops)" in out and "Jarvis: ok" in out


def test_llm_prompt_uses_custom_name(monkeypatch):
    from matrix import llm
    seen = {}

    class Fake:
        def invoke(self, prompt):
            seen["prompt"] = prompt
            return type("R", (), {"content": "hi"})()

    monkeypatch.setattr(llm, "get_llm", lambda: Fake())
    names.save(rename(names.defaults(), "ASSISTANT", display="Jarvis"))
    llm.write("TAPER (field ops)", "hello", "mock")
    assert seen["prompt"].startswith("You are Jarvis,") and "only as Jarvis" in seen["prompt"]


def test_panel_server(names_file):
    import rename_panel
    server = rename_panel.ThreadingHTTPServer(("127.0.0.1", 0), rename_panel.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "Rename panel" in page
        data = json.loads(urllib.request.urlopen(base + "/names").read())
        rename(data, "ASSISTANT", display="Jarvis")
        req = urllib.request.Request(base + "/names", json.dumps(data).encode(), {"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())["agents"][0]["display"] == "Jarvis"
        assert names.assistant_name() == "Jarvis"
        bad = urllib.request.Request(base + "/names", json.dumps(rename(data, "ASSISTANT", display="")).encode(),
                                     {"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad)
        assert err.value.code == 400
        evil = urllib.request.Request(base + "/reset", b"{}", {"Content-Type": "application/json",
                                                               "Origin": "http://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(evil)
        assert err.value.code == 403
        reset = urllib.request.Request(base + "/reset", b"{}", {"Content-Type": "application/json"})
        urllib.request.urlopen(reset)
        assert names.assistant_name() == "Stuart"
    finally:
        server.shutdown()
        server.server_close()
