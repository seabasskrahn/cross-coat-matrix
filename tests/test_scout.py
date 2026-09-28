"""Scout (Tavily web search): when it triggers, the caps, what gets recorded. Never calls Tavily."""
import json
import sys
import types

import pytest

from matrix import config, envelope as env, graph as g, keeper_worker as kw, llm, scout
from matrix.keeper_store import MemoryStore

SECRET = "tvly-test-not-a-real-key"
NEEDS = "Look up the current price of 1/2 inch drywall sheets in Alberta"


@pytest.fixture
def searches(monkeypatch):
    """Scout switched on with a fake search; returns the list of queries it ran."""
    ran = []
    monkeypatch.setenv("TAVILY_API_KEY", SECRET)
    monkeypatch.setattr(scout, "enabled", lambda: True)
    monkeypatch.setattr(scout, "run_search", lambda q: ran.append(q) or [
        {"title": "Drywall prices", "url": "https://example.com/drywall", "content": "About $20 a sheet."}])
    monkeypatch.setattr(config, "SCOUT_MAX_SEARCHES_PER_JOB", 2)
    return ran


def test_enabled_needs_key_cap_and_real_brain(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "SCOUT_MAX_SEARCHES_PER_JOB", 2)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert not scout.enabled()                      # no key: off entirely
    monkeypatch.setenv("TAVILY_API_KEY", SECRET)
    assert scout.enabled()
    monkeypatch.setattr(config, "SCOUT_MAX_SEARCHES_PER_JOB", 0)
    assert not scout.enabled()                      # cap 0: off
    monkeypatch.setattr(config, "SCOUT_MAX_SEARCHES_PER_JOB", 2)
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    assert not scout.enabled()                      # mock mode: off


def test_no_key_means_no_search(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(scout, "run_search", lambda q: pytest.fail("searched without a key"))
    assert scout.research(NEEDS, [1], []) is None


def test_only_clear_research_tasks_trigger():
    for text in [NEEDS, "research the Alberta building code for fire-rated walls",
                 "find out the latest QuickBooks API release notes", "what's the going rate for taping?"]:
        assert scout.needs_search(text), text
    for text in ["Tailgate briefing for the crew tomorrow 7:30 am", "quote the Reimer reno",
                 "Silverado oil change", "Send the Friesen invoice reminder"]:
        assert not scout.needs_search(text), text


def test_query_is_the_task_trimmed():
    assert scout.make_query("Please look up:  the latest\n  Google Gemini models") == "the latest Google Gemini models"
    assert scout.make_query("look up " + "x" * 1000).startswith("x") and len(scout.make_query("x" * 1000)) == 300


def test_one_search_per_task_and_per_job_cap(searches):
    log = []
    first = scout.research(NEEDS, [1], log)
    assert first["ok"] and first["results"] == 1 and "About $20 a sheet." in first["extra"]
    log.append(env.entry(env.SCOUT_SEARCH, **scout.step_fields(first, [1], "MARGIN")))
    assert scout.research(NEEDS, [1], log) is None             # task 1 already searched
    second = scout.research(NEEDS, [2], log)
    log.append(env.entry(env.SCOUT_SEARCH, **scout.step_fields(second, [2], "MARGIN")))
    assert scout.research(NEEDS, [3], log) is None             # job cap (2) reached
    assert len(searches) == 2


def test_failed_search_is_recorded_and_draft_continues(monkeypatch, searches):
    def boom(q):
        raise ConnectionError("down")
    monkeypatch.setattr(scout, "run_search", boom)
    found = scout.research(NEEDS, [1], [])
    assert found["ok"] is False and found["extra"] == ""
    assert scout.step_fields(found, [1], "MARGIN")["error"] == "ConnectionError"


def test_run_search_parses_tavily_without_network(monkeypatch):
    calls = []

    class FakeTavily:
        def __init__(self, **kw):
            calls.append(kw)

        def invoke(self, args):
            calls.append(args)
            return {"results": [{"title": "T", "url": "https://u", "content": "C", "score": 0.9}]}

    monkeypatch.setitem(sys.modules, "langchain_tavily", types.SimpleNamespace(TavilySearch=FakeTavily))
    assert scout.run_search("drywall price") == [{"title": "T", "url": "https://u", "content": "C"}]
    assert calls[0]["search_depth"] == "basic" and calls[1] == {"query": "drywall price"}


def test_graph_drafting_uses_scout_and_records_query_only(monkeypatch, searches):
    prompts = []
    real = llm.write
    monkeypatch.setattr(llm, "write", lambda role, text, mock, **kw: prompts.append(text) or real(role, text, mock, **kw))
    state = {"message": NEEDS, "agents": [],
             "tasks": [{"num": 1, "title": NEEDS, "depends_on": [], "agent": {"name": "MARGIN", "role": "pricing"}}]}
    out = g.make_specialist("MARGIN")(state)
    assert searches == [scout.make_query(NEEDS)]
    s = [e for e in out["step_log"] if e["tag"] == env.SCOUT_SEARCH]
    assert len(s) == 1 and s[0]["query"] == searches[0] and s[0]["agent"] == "MARGIN" and s[0]["task"] == 1
    assert out["drafts"][0]["scout"] == {"query": searches[0]}
    assert "About $20 a sheet." in prompts[0]
    assert SECRET not in json.dumps(out) and "api_key" not in json.dumps(out).lower()


def test_graph_caps_searches_per_job(monkeypatch, searches):
    tasks = [{"num": i, "title": f"look up the latest {w} prices", "depends_on": [], "agent": {"name": a, "role": "x"}}
             for i, (w, a) in enumerate([("tape", "TAPER"), ("oil", "ARMOR"), ("board", "MARGIN")], start=1)]
    out = g.make_specialist("TAPER")({"message": "three lookups", "agents": [], "tasks": tasks})
    assert len(searches) == 2 == [e["tag"] for e in out["step_log"]].count(env.SCOUT_SEARCH)
    assert len(out["drafts"]) == 3 and "scout" not in out["drafts"][2]


def test_no_search_when_task_does_not_need_it(searches):
    out = g.make_specialist("TAPER")({"message": "Tailgate briefing for the crew tomorrow", "agents": [],
                                      "tasks": [{"num": 1, "title": "Tailgate briefing for the crew tomorrow",
                                                 "depends_on": []}]})
    assert searches == [] and env.SCOUT_SEARCH not in [e["tag"] for e in out["step_log"]]


def test_outward_draft_with_search_still_needs_approval(searches):
    msg = "Look up the latest drywall prices and email them to Friesen"
    out = g.make_specialist("MARGIN")({"message": msg, "agents": [], "tasks": [
        {"num": 1, "title": msg, "depends_on": [], "agent": {"name": "MARGIN", "role": "pricing"}}]})
    assert len(searches) == 1
    assert out["proposed_action"]["outward"] is True and g.needs_approval(out) == "ask_approval"


class FakeBrain:
    def __init__(self):
        self.calls = []

    def __call__(self, name, role, text):
        self.calls.append(text)
        return f"{name} draft"


def test_worker_searches_once_per_task_and_still_asks_approval(searches):
    j = {"id": 7, "message": NEEDS, "agents": [{"name": "MARGIN", "role": "pricing"}], "drafts": [],
         "approvals": [], "step_log": [], "tasks": [{"num": 1, "title": NEEDS, "depends_on": []}]}
    store, brain = MemoryStore([j]), FakeBrain()
    kw.sweep_once(store, brain)
    row = store.rows[7]
    tags = [e["tag"] for e in row["step_log"]]
    assert tags.count(env.SCOUT_SEARCH) == 1 and tags[-1] == env.APPROVAL_ASKED
    assert row["drafts"][0]["scout"] == {"query": scout.make_query(NEEDS)}
    assert "About $20 a sheet." in brain.calls[0]
    assert row["approvals"][-1]["status"] == "pending"   # the owner still decides
    assert SECRET not in json.dumps(row)
    # A context-request re-draft of the same task does not search again.
    row["approvals"][-1].update(type="context_request", text="Which supplier?", status="answered",
                                answer="Kent", answered_at="2000-01-01T00:00:00Z")
    kw.sweep_once(store, brain)
    assert [e["tag"] for e in store.rows[7]["step_log"]].count(env.SCOUT_SEARCH) == 1 and len(brain.calls) == 2
