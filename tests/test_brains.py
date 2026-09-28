"""Per-agent brains: defaults, one-line overrides, graceful fallback. No network: models are faked."""
import json
import logging
from pathlib import Path

import pytest

from matrix import agents, config, envelope as env, graph as g, llm


class FakeModel:
    def __init__(self, provider, model):
        self.provider, self.model, self.prompts = provider, model, []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("R", (), {"content": f"draft by {self.provider}:{self.model}"})()


@pytest.fixture
def live(monkeypatch):
    """Pretend the default brain is Gemini with both keys present; building a model is faked."""
    for name in list(__import__("os").environ):
        if name.startswith("BRAIN_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setenv("XAI_API_KEY", "test-xai")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_llms", {})
    monkeypatch.setattr(llm, "_warned", set())
    built = []
    monkeypatch.setattr(llm, "_make", lambda p, m: built.append((p, m)) or FakeModel(p, m))
    return built


def test_default_brain_per_agent(live):
    assert llm.brain_for("STEWARD") == ("xai", config.XAI_MODEL)
    assert llm.brain_for("BEZEL") == ("gemini", config.GEMINI_MODEL)
    for other in ["SUNDAY", "TAPER", "MARGIN", "LEDGER", "SOMEONE_NEW", None]:
        assert llm.brain_for(other) == ("gemini", config.GEMINI_MODEL)  # inherit the global default


def test_default_follows_global_setting(live, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "xai")
    assert llm.brain_for("TAPER") == ("xai", config.XAI_MODEL)
    assert llm.brain_for("BEZEL") == ("gemini", config.GEMINI_MODEL)  # BEZEL keeps its own brain


def test_env_line_overrides_one_agent(live, monkeypatch):
    monkeypatch.setenv("BRAIN_TAPER", "xai:grok-4.3")
    monkeypatch.setenv("BRAIN_STEWARD", "gemini")
    assert llm.brain_for("TAPER") == ("xai", "grok-4.3")
    assert llm.brain_for("STEWARD") == ("gemini", config.GEMINI_MODEL)
    assert llm.brain_for("ARMOR") == ("gemini", config.GEMINI_MODEL)  # nobody else changes


def test_config_line_overrides_one_agent(live, monkeypatch):
    monkeypatch.setitem(config.AGENT_BRAINS, "VECTOR", "grok")
    assert llm.brain_for("VECTOR") == ("xai", config.XAI_MODEL)


def test_missing_key_falls_back_with_warning(live, monkeypatch, caplog):
    monkeypatch.delenv("XAI_API_KEY")
    with caplog.at_level(logging.WARNING, logger="matrix.llm"):
        assert llm.brain_for("STEWARD") == ("gemini", config.GEMINI_MODEL)
        model = llm.get_llm("STEWARD")
    assert model.provider == "gemini"
    assert "no API key for xai" in caplog.text and "STEWARD" in caplog.text
    assert "test-" not in caplog.text  # never logs a key


def test_unknown_provider_falls_back(live, monkeypatch, caplog):
    monkeypatch.setenv("BRAIN_FINISH", "skynet:v9")
    with caplog.at_level(logging.WARNING, logger="matrix.llm"):
        assert llm.brain_for("FINISH") == ("gemini", config.GEMINI_MODEL)
    assert "unknown provider" in caplog.text


def test_agent_brain_that_cannot_start_falls_back(live, monkeypatch, caplog):
    def make(p, m):
        if p == "xai":
            raise ImportError("no package")
        return FakeModel(p, m)
    monkeypatch.setattr(llm, "_make", make)
    with caplog.at_level(logging.WARNING, logger="matrix.llm"):
        assert llm.get_llm("STEWARD").provider == "gemini"
    assert "could not start" in caplog.text


def test_mock_is_master_offline_switch(live, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    assert llm.brain_for("STEWARD") == ("mock", "")
    assert llm.get_llm("STEWARD") is None and llm.get_llm("BEZEL") is None and live == []


def test_models_are_shared_per_brain(live):
    assert llm.get_llm("TAPER") is llm.get_llm("ARMOR")  # same default brain, built once
    assert llm.get_llm("STEWARD") is not llm.get_llm("BEZEL")
    assert sorted(set(live)) == [("gemini", config.GEMINI_MODEL), ("xai", config.XAI_MODEL)]


def test_each_agent_drafts_with_its_own_brain(live):
    assert env.brain("STEWARD", "senior staff", "plan it", "mock") == f"draft by xai:{config.XAI_MODEL}"
    assert env.brain("BEZEL", "senior staff", "plan it", "mock") == f"draft by gemini:{config.GEMINI_MODEL}"
    assert env.brain("MARGIN", "pricing", "price it", "mock") == f"draft by gemini:{config.GEMINI_MODEL}"


def test_router_and_seniors_classify_with_their_brains(monkeypatch):
    seen = []
    real = llm.classify
    monkeypatch.setattr(llm, "classify", lambda *a, agent=None, **k: seen.append(agent) or real(*a, agent=agent, **k))
    g.sunday({"message": "quote the Reimer reno"})
    g.make_senior("STEWARD")({"message": "quote the Reimer reno", "tasks": [{"num": 1, "title": "x", "depends_on": []}]})
    assert seen == ["SUNDAY", "STEWARD"]


def test_offline_by_default_in_tests():
    assert llm.brain_for("STEWARD") == ("mock", "") and llm.get_llm("STEWARD") is None


def test_every_agent_in_names_file_is_in_the_roster():
    """An agent listed in matrix_names.json (senior or specialist) must exist in matrix/agents.py."""
    data = json.loads((Path(__file__).resolve().parent.parent / "matrix_names.json").read_text(encoding="utf-8"))
    listed = {a["id"] for a in data["agents"] if a.get("kind") in ("senior", "specialist")}
    assert listed <= set(agents.SENIOR_STAFF) | set(agents.SPECIALISTS)
