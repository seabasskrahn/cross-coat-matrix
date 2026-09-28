"""MAIN BRAIN switch: .env is re-read every worker sweep; invalid values keep the previous brain.
Uses temporary .env files only (never the real one) and never calls an AI."""
import logging
import os
from pathlib import Path

import pytest

from matrix import config, keeper_worker as kw, llm

HEADER = "# ===== MAIN BRAIN: change to gemini or xai =====\n"
SECRET = "test-secret-do-not-log"


@pytest.fixture
def envfile(tmp_path, monkeypatch):
    saved = dict(os.environ)
    for name in [n for n in os.environ if n.startswith("BRAIN_")]:
        monkeypatch.delenv(name)
    for attr in ("LLM_PROVIDER", "GEMINI_MODEL", "XAI_MODEL", "CLAUDE_MODEL",
                 "SCOUT_MAX_SEARCHES_PER_JOB", "SCOUT_MAX_RESULTS"):
        monkeypatch.setattr(config, attr, getattr(config, attr))
    monkeypatch.setattr(config, "DEFAULT_MODELS", dict(config.DEFAULT_MODELS))
    monkeypatch.setattr(config, "_file_keys", set())
    monkeypatch.setattr(llm, "_warned", set())
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "_env_file", path)

    def write(provider, extra=""):
        path.write_text(f"{HEADER}LLM_PROVIDER={provider}\n\nGOOGLE_API_KEY={SECRET}\n"
                        f"XAI_API_KEY={SECRET}\nGEMINI_MODEL=gemini-test\nXAI_MODEL=grok-test\n{extra}",
                        encoding="utf-8")
        return path
    yield write
    os.environ.clear()
    os.environ.update(saved)


def test_switch_takes_effect_on_reload(envfile, caplog):
    envfile("gemini")
    config.reload_env()
    assert config.LLM_PROVIDER == "gemini" and llm.brain_for("TAPER") == ("gemini", "gemini-test")
    envfile("xai")
    with caplog.at_level(logging.INFO, logger="matrix.config"):
        assert config.reload_env() is True
    assert "main brain changed: gemini -> xai" in caplog.text
    assert llm.brain_for("TAPER") == ("xai", "grok-test") and llm.brain_for("SUNDAY") == ("xai", "grok-test")
    assert llm.brain_for("BEZEL") == ("gemini", "gemini-test")   # pinned in config.AGENT_BRAINS
    assert config.reload_env() is False                          # nothing changed: no new log line
    assert SECRET not in caplog.text


def test_pinned_steward_stays_on_grok_unless_env_line(envfile):
    envfile("gemini")
    config.reload_env()
    assert llm.brain_for("STEWARD") == ("xai", "grok-test")
    envfile("gemini", "BRAIN_STEWARD=gemini\n")
    config.reload_env()
    assert llm.brain_for("STEWARD") == ("gemini", "gemini-test")
    envfile("gemini")                                            # line removed again
    config.reload_env()
    assert "BRAIN_STEWARD" not in os.environ and llm.brain_for("STEWARD") == ("xai", "grok-test")


def test_invalid_value_keeps_previous_brain(envfile, caplog):
    envfile("xai")
    config.reload_env()
    envfile("chatgpt")
    with caplog.at_level(logging.WARNING, logger="matrix.config"):
        assert config.reload_env() is False
    assert config.LLM_PROVIDER == "xai" and llm.brain_for("TAPER")[0] == "xai"
    assert "not valid" in caplog.text and "keeping xai" in caplog.text


def test_invalid_value_at_startup_falls_back_to_default(caplog):
    with caplog.at_level(logging.WARNING, logger="matrix.config"):
        assert config._valid_provider("banana", "mock") == "mock"
    assert config._valid_provider(" Gemini ", "mock") == "gemini"
    assert "not valid" in caplog.text


def test_mock_switch_turns_everyone_offline(envfile):
    envfile("mock")
    config.reload_env()
    assert llm.brain_for("STEWARD") == ("mock", "") and llm.get_llm("BEZEL") is None


def test_worker_reload_logs_brains_and_never_crashes(envfile, monkeypatch, caplog):
    envfile("gemini")
    config.reload_env()
    envfile("xai")
    with caplog.at_level(logging.INFO, logger="keeper_worker"):
        assert kw.reload_settings() is True
    assert "brains: main=xai" in caplog.text and SECRET not in caplog.text
    monkeypatch.setattr(config, "reload_env", lambda: (_ for _ in ()).throw(OSError("locked")))
    assert kw.reload_settings() is False


def test_worker_rereads_env_every_sweep(monkeypatch):
    calls = []
    monkeypatch.setattr(kw, "reload_settings", lambda: calls.append("reload"))
    monkeypatch.setattr(kw, "setup_logging", lambda: None)
    monkeypatch.setattr(kw, "log_brains", lambda: None)
    monkeypatch.setattr(config, "CHECKPOINT_DB_URL", "postgresql://fake")

    class FakeStore:
        def __init__(self, url):
            pass

        def close(self):
            pass

    monkeypatch.setattr(kw, "PgStore", FakeStore)
    monkeypatch.setattr(kw, "sweep_once", lambda store: calls.append("sweep") or kw.Sweep())
    assert kw.main(["--once"]) == 0
    assert calls == ["reload", "sweep"]


def test_env_example_has_the_main_brain_switch_first():
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0] == HEADER.strip()
    first_setting = next(ln for ln in lines if not ln.startswith("#"))
    assert first_setting.startswith("LLM_PROVIDER=")


def test_worker_logs_brains_when_only_an_agent_brain_changes(envfile, caplog):
    envfile("xai")
    config.reload_env()
    envfile("xai", "BRAIN_BEZEL=xai\n")
    with caplog.at_level(logging.INFO, logger="keeper_worker"):
        assert kw.reload_settings() is True                  # main brain unchanged, BEZEL changed
    assert "BEZEL=xai:grok-test" in caplog.text and llm.brain_for("BEZEL") == ("xai", "grok-test")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="keeper_worker"):
        assert kw.reload_settings() is False                 # nothing changed: no new line
    assert "brains:" not in caplog.text
