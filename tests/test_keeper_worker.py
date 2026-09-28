"""Keeper worker tests: in-memory store + fake brain, no network."""
import pytest

from matrix import envelope as env
from matrix import keeper_worker as kw
from matrix.keeper_store import MemoryStore


class FakeBrain:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, name, role, text):
        self.calls.append((name, role, text))
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("brain exploded")
        return f"```\n{name} draft #{len(self.calls)}\n```"


def job(id=2, tasks=None, agents=None, approvals=None, step_log=None, drafts=None):
    return {"id": id, "message": "Write a welcome note",
            "tasks": tasks if tasks is not None else [{"num": 1, "title": "Write it", "depends_on": []}],
            "agents": agents if agents is not None else [{"name": "BEZEL", "role": "writer"}],
            "drafts": drafts or [], "approvals": approvals or [], "step_log": step_log or []}


def tag_list(j):
    return [e["tag"] for e in j["step_log"]]


# ---------- pending detection ----------
def test_pending_detection():
    assert env.is_pending(job())
    assert not env.is_pending(job(step_log=[{"tag": "job closed", "at": "x"}]))
    assert not env.is_pending(job(approvals=[{"type": "yes_no", "status": "pending"}]))
    assert env.is_pending(job(approvals=[{"type": "yes_no", "status": "yes"}]))
    assert not env.is_pending(job(step_log=[{"tag": "circuit breaker", "at": "x"}]))
    assert not env.is_pending(job(step_log=[{"tag": "error: boom", "at": "x"}]))
    # owner can resume a halted job by appending anything after the halt tag
    assert env.is_pending(job(step_log=[{"tag": "circuit breaker"}, {"tag": "resume"}]))


def test_row1_with_pending_approval_is_skipped():
    row1 = job(id=1, approvals=[{"from": "user", "to": "system", "type": "yes_no",
                                 "text": "Approve this layout?", "status": "pending"}])
    store, brain = MemoryStore([row1]), FakeBrain()
    kw.sweep_once(store, brain)
    assert store.claims == [] and brain.calls == []
    assert store.rows[1] == row1


def test_locked_job_is_skipped():
    store, brain = MemoryStore([job()]), FakeBrain()
    store.lock(2)  # e.g. the main Matrix flow is working on it
    kw.sweep_once(store, brain)
    assert brain.calls == [] and store.rows[2]["step_log"] == []


# ---------- dependency ordering ----------
def test_topo_order():
    tasks = [{"num": 1, "depends_on": [3]}, {"num": 2, "depends_on": []}, {"num": 3, "depends_on": [2]}]
    assert [tasks[i]["num"] for i in env.topo_order(tasks)] == [2, 3, 1]


def test_cycle_halts_job():
    tasks = [{"num": 1, "depends_on": [2]}, {"num": 2, "depends_on": [1]}]
    store, brain = MemoryStore([job(tasks=tasks)]), FakeBrain()
    kw.sweep_once(store, brain)
    assert tag_list(store.rows[2])[-1] == "dependency cycle"
    assert brain.calls == []
    assert not env.is_pending(store.rows[2])


def test_missing_dependency_raises():
    with pytest.raises(env.DependencyCycle):
        env.topo_order([{"num": 1, "depends_on": [9]}])


def test_runs_in_order_and_skips_already_drafted():
    tasks = [{"num": 1, "title": "A", "depends_on": [2]}, {"num": 2, "title": "B", "depends_on": []},
             {"num": 3, "title": "C", "depends_on": [1]}]
    agents = [{"name": "LUMEN", "role": "designer"}, {"name": "SUNDAY", "role": "builder"},
              {"name": "Merovingian", "role": "reviewer"}]
    drafts = [{"task": 2, "agent": "SUNDAY", "output": "done already"}]
    store, brain = MemoryStore([job(tasks=tasks, agents=agents, drafts=drafts)]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.rows[2]
    assert [d["task"] for d in j["drafts"]] == [2, 1, 3]
    assert [c[0] for c in brain.calls] == ["LUMEN", "Merovingian"]


# ---------- routing ----------
def test_routing_rules():
    agents = [{"name": "LUMEN", "role": "designer"}]
    assert env.route({"agent": "TAPER", "role": "crew"}, 0, agents) == ("TAPER", "crew", "task")
    assert env.route({"agent": {"name": "X", "role": "r"}}, 0, agents) == ("X", "r", "task")
    assert env.route({"agent": "LUMEN"}, 5, agents) == ("LUMEN", "designer", "task")
    assert env.route({}, 0, agents) == ("LUMEN", "designer", "index")
    assert env.route({}, 1, agents) == ("STEWARD", "senior staff", "senior fallback")


def test_unknown_agent_is_tagged_and_still_drafted():
    agents = [{"name": "Merovingian", "role": "reviewer"}]
    store, brain = MemoryStore([job(agents=agents)]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.rows[2]
    assert "agent not in roster" in tag_list(j)
    assert brain.calls[0][:2] == ("Merovingian", "reviewer")
    assigned = next(e for e in j["step_log"] if e["tag"] == "agent assigned")
    assert assigned["agent"] == "Merovingian" and assigned["role"] == "reviewer"


def test_roster_agent_not_tagged():
    store, brain = MemoryStore([job()]), FakeBrain()  # BEZEL is in agents.py
    kw.sweep_once(store, brain)
    assert "agent not in roster" not in tag_list(store.rows[2])


def test_default_brain_prompt_for_unknown_agent(monkeypatch):
    from matrix import llm
    seen = {}
    monkeypatch.setattr(llm, "write", lambda who, text, mock: seen.setdefault("who", who) and "ok")
    kw.default_brain("Merovingian", "reviewer", "Check it")
    assert seen["who"] == "Merovingian, the team's reviewer"


# ---------- drafts + approval wait ----------
def test_full_flow_ends_waiting_for_approval():
    store, brain = MemoryStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.rows[2]
    assert tag_list(j) == ["picked up", "message received", "task created", "agent assigned",
                           "draft saved", "approval asked"]
    d = j["drafts"][0]
    assert d["task"] == 1 and d["agent"] == "BEZEL" and d["role"] == "writer"
    assert d["output"].startswith("```")  # stored as-is
    a = j["approvals"][-1]
    assert a["from"] == "system" and a["type"] == "yes_no" and a["status"] == "pending"
    assert all(e["at"].endswith("Z") for e in j["step_log"])
    # next sweep: still waiting, nothing happens, no auto-approve
    kw.sweep_once(store, brain)
    assert len(brain.calls) == 1 and tag_list(store.rows[2])[-1] == "approval asked"
    assert store.rows[2]["approvals"][-1]["status"] == "pending"


def test_answered_yes_closes():
    store, brain = MemoryStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    store.rows[2]["approvals"][-1]["status"] = "yes"
    kw.sweep_once(store, brain)
    t = tag_list(store.rows[2])
    assert t[-2:] == ["approval answered", "job closed"] and "rejected" not in t
    assert len(brain.calls) == 1
    kw.sweep_once(store, brain)  # closed jobs are left alone
    assert store.claims == [2, 2]


def test_answered_no_rejects_and_closes():
    store, brain = MemoryStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    store.rows[2]["approvals"][-1]["status"] = "no"
    kw.sweep_once(store, brain)
    assert tag_list(store.rows[2])[-3:] == ["approval answered", "rejected", "job closed"]


def test_context_request_answer_triggers_redraft():
    store, brain = MemoryStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.rows[2]
    j["approvals"][-1]["status"] = "answered"
    j["approvals"].append({"from": "system", "to": "user", "type": "context_request",
                           "text": "Formal or casual?", "status": "answered", "answer": "Casual"})
    kw.sweep_once(store, brain)
    j = store.rows[2]
    assert "Casual" in brain.calls[-1][2]
    assert j["drafts"][-1]["for_approval"] == 1
    answered = [e for e in j["step_log"] if e["tag"] == "approval answered"]
    assert answered[-1]["type"] == "context request"
    assert tag_list(j)[-1] == "approval asked" and j["approvals"][-1]["status"] == "pending"


# ---------- circuit breaker ----------
def test_task_cap_per_job():
    tasks = [{"num": i, "title": f"t{i}", "depends_on": []} for i in range(1, 8)]
    store, brain = MemoryStore([job(tasks=tasks)]), FakeBrain()
    kw.sweep_once(store, brain, kw.Sweep(max_llm_calls=100, max_tasks_per_job=5))
    assert len(brain.calls) == 5 and tag_list(store.rows[2])[-1] == "circuit breaker"
    assert not env.is_pending(store.rows[2])  # left for the owner
    kw.sweep_once(store, brain, kw.Sweep(max_llm_calls=100))
    assert len(brain.calls) == 5


def test_llm_call_cap_per_sweep():
    tasks = [{"num": i, "title": f"t{i}", "depends_on": []} for i in range(1, 4)]
    store, brain = MemoryStore([job(tasks=tasks), job(id=3)]), FakeBrain()
    kw.sweep_once(store, brain, kw.Sweep(max_llm_calls=2))
    assert len(brain.calls) == 2
    assert tag_list(store.rows[2])[-1] == "circuit breaker"
    assert store.rows[3]["step_log"] == []  # untouched, waits for a later sweep


def test_error_is_logged_and_halts():
    store, brain = MemoryStore([job()]), FakeBrain(fail_on="Write it")
    kw.sweep_once(store, brain)
    last = tag_list(store.rows[2])[-1]
    assert last.startswith("error: RuntimeError") and not env.is_pending(store.rows[2])


def test_legacy_draft_without_task_number_counts_for_first_task():
    tasks = [{"num": 1, "title": "Design", "depends_on": []}, {"num": 2, "title": "Wire", "depends_on": [1]}]
    agents = [{"name": "LUMEN", "role": "designer"}, {"name": "SUNDAY", "role": "builder"}]
    drafts = [{"agent": "LUMEN", "output": "# Layout"}]
    approvals = [{"from": "user", "to": "system", "type": "yes_no", "status": "yes"}]
    store, brain = MemoryStore([job(tasks=tasks, agents=agents, drafts=drafts, approvals=approvals)]), FakeBrain()
    kw.sweep_once(store, brain)
    assert [c[0] for c in brain.calls] == ["SUNDAY"]
    assert tag_list(store.rows[2])[-1] == "approval asked"


def test_batched_draft_covers_all_its_tasks():
    tasks = [{"num": 1, "title": "A", "depends_on": []}, {"num": 2, "title": "B", "depends_on": []}]
    drafts = [{"task": 1, "covers": [1, 2], "agent": "BEZEL", "output": "both"}]
    store, brain = MemoryStore([job(tasks=tasks, drafts=drafts)]), FakeBrain()
    kw.sweep_once(store, brain)
    assert brain.calls == [] and tag_list(store.rows[2])[-1] == "approval asked"
