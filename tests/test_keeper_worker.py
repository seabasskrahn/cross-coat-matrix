"""Keeper worker tests: fake database + fake brain, no network."""
import copy
import os

os.environ["LLM_PROVIDER"] = "mock"

import pytest  # noqa: E402

from matrix import keeper_worker as kw  # noqa: E402


class FakeStore:
    """In-memory stand-in for PgStore with the same methods."""

    def __init__(self, jobs):
        self.jobs = {j["id"]: j for j in copy.deepcopy(jobs)}
        self.locked = set()
        self.claims = []

    def candidates(self):
        return [copy.deepcopy(j) for _, j in sorted(self.jobs.items())]

    def claim(self, job_id):
        if job_id in self.locked or not kw.is_pending(self.jobs[job_id]):
            return None
        self.locked.add(job_id)
        self.claims.append(job_id)
        self.append(self.jobs[job_id], "step_log", kw.entry(kw.PICKED_UP))
        return self.jobs[job_id]

    def append(self, job, column, item):
        job[column] = list(job.get(column) or []) + [item]
        self.jobs[job["id"]] = job

    def release(self, job_id):
        self.locked.discard(job_id)


class FakeBrain:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, name, role, in_roster, text):
        self.calls.append((name, role, in_roster, text))
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
    assert kw.is_pending(job())
    assert not kw.is_pending(job(step_log=[{"tag": "job closed", "at": "x"}]))
    assert not kw.is_pending(job(approvals=[{"type": "yes_no", "status": "pending"}]))
    assert kw.is_pending(job(approvals=[{"type": "yes_no", "status": "yes"}]))
    assert not kw.is_pending(job(step_log=[{"tag": "circuit breaker", "at": "x"}]))
    assert not kw.is_pending(job(step_log=[{"tag": "error: boom", "at": "x"}]))
    # owner can resume a halted job by appending anything after the halt tag
    assert kw.is_pending(job(step_log=[{"tag": "circuit breaker"}, {"tag": "resume"}]))


def test_row1_with_pending_approval_is_skipped():
    row1 = job(id=1, approvals=[{"from": "user", "to": "system", "type": "yes_no",
                                 "text": "Approve this layout?", "status": "pending"}])
    store, brain = FakeStore([row1]), FakeBrain()
    kw.sweep_once(store, brain)
    assert store.claims == [] and brain.calls == []
    assert store.jobs[1] == row1


# ---------- dependency ordering ----------
def test_topo_order():
    tasks = [{"num": 1, "depends_on": [3]}, {"num": 2, "depends_on": []}, {"num": 3, "depends_on": [2]}]
    assert [tasks[i]["num"] for i in kw.topo_order(tasks)] == [2, 3, 1]


def test_cycle_halts_job():
    tasks = [{"num": 1, "depends_on": [2]}, {"num": 2, "depends_on": [1]}]
    store, brain = FakeStore([job(tasks=tasks)]), FakeBrain()
    kw.sweep_once(store, brain)
    assert tag_list(store.jobs[2])[-1] == "dependency cycle"
    assert brain.calls == []
    assert not kw.is_pending(store.jobs[2])


def test_missing_dependency_raises():
    with pytest.raises(kw.DependencyCycle):
        kw.topo_order([{"num": 1, "depends_on": [9]}])


def test_runs_in_order_and_skips_already_drafted():
    tasks = [{"num": 1, "title": "A", "depends_on": [2]}, {"num": 2, "title": "B", "depends_on": []},
             {"num": 3, "title": "C", "depends_on": [1]}]
    agents = [{"name": "LUMEN", "role": "designer"}, {"name": "SUNDAY", "role": "builder"},
              {"name": "Merovingian", "role": "reviewer"}]
    drafts = [{"task": 2, "agent": "SUNDAY", "output": "done already"}]
    store, brain = FakeStore([job(tasks=tasks, agents=agents, drafts=drafts)]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.jobs[2]
    assert [d["task"] for d in j["drafts"]] == [2, 1, 3]
    assert [c[0] for c in brain.calls] == ["LUMEN", "Merovingian"]


# ---------- routing ----------
def test_routing_rules():
    agents = [{"name": "LUMEN", "role": "designer"}]
    assert kw.route({"agent": "TAPER", "role": "crew"}, 0, agents) == ("TAPER", "crew", "task")
    assert kw.route({"agent": {"name": "X", "role": "r"}}, 0, agents) == ("X", "r", "task")
    assert kw.route({"agent": "LUMEN"}, 5, agents) == ("LUMEN", "designer", "task")
    assert kw.route({}, 0, agents) == ("LUMEN", "designer", "index")
    assert kw.route({}, 1, agents) == ("STEWARD", "senior", "senior fallback")


def test_unknown_agent_is_tagged_and_still_drafted():
    agents = [{"name": "Merovingian", "role": "reviewer"}]
    store, brain = FakeStore([job(agents=agents)]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.jobs[2]
    assert "agent not in roster" in tag_list(j)
    assert brain.calls[0][:3] == ("Merovingian", "reviewer", False)
    assigned = next(e for e in j["step_log"] if e["tag"] == "agent assigned")
    assert assigned["agent"] == "Merovingian" and assigned["role"] == "reviewer"


def test_roster_agent_not_tagged():
    store, brain = FakeStore([job()]), FakeBrain()  # BEZEL is in agents.py
    kw.sweep_once(store, brain)
    assert "agent not in roster" not in tag_list(store.jobs[2])
    assert brain.calls[0][2] is True


# ---------- drafts + approval wait ----------
def test_full_flow_ends_waiting_for_approval():
    store, brain = FakeStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.jobs[2]
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
    assert len(brain.calls) == 1 and tag_list(j)[-1] == "approval asked"
    assert j["approvals"][-1]["status"] == "pending"


def test_answered_yes_closes():
    store, brain = FakeStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    store.jobs[2]["approvals"][-1]["status"] = "yes"
    kw.sweep_once(store, brain)
    t = tag_list(store.jobs[2])
    assert t[-2:] == ["approval answered", "job closed"] and "rejected" not in t
    assert len(brain.calls) == 1
    kw.sweep_once(store, brain)  # closed jobs are left alone
    assert store.claims == [2, 2]


def test_answered_no_rejects_and_closes():
    store, brain = FakeStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    store.jobs[2]["approvals"][-1]["status"] = "no"
    kw.sweep_once(store, brain)
    assert tag_list(store.jobs[2])[-3:] == ["approval answered", "rejected", "job closed"]


def test_context_request_answer_triggers_redraft():
    store, brain = FakeStore([job()]), FakeBrain()
    kw.sweep_once(store, brain)
    j = store.jobs[2]
    j["approvals"][-1]["status"] = "answered"  # owner turns it into a context exchange
    j["approvals"].append({"from": "system", "to": "user", "type": "context request",
                           "text": "Formal or casual?", "status": "answered", "answer": "Casual"})
    kw.sweep_once(store, brain)
    assert "Casual" in brain.calls[-1][3]
    assert j["drafts"][-1]["for_approval"] == 1
    assert tag_list(j)[-1] == "approval asked" and j["approvals"][-1]["status"] == "pending"


# ---------- circuit breaker ----------
def test_task_cap_per_job():
    tasks = [{"num": i, "title": f"t{i}", "depends_on": []} for i in range(1, 8)]
    store, brain = FakeStore([job(tasks=tasks)]), FakeBrain()
    kw.sweep_once(store, brain, kw.Sweep(max_llm_calls=100, max_tasks_per_job=5))
    j = store.jobs[2]
    assert len(brain.calls) == 5 and tag_list(j)[-1] == "circuit breaker"
    assert not kw.is_pending(j)  # left for the owner
    kw.sweep_once(store, brain, kw.Sweep(max_llm_calls=100))
    assert len(brain.calls) == 5


def test_llm_call_cap_per_sweep():
    tasks = [{"num": i, "title": f"t{i}", "depends_on": []} for i in range(1, 4)]
    store, brain = FakeStore([job(tasks=tasks), job(id=3)]), FakeBrain()
    kw.sweep_once(store, brain, kw.Sweep(max_llm_calls=2))
    assert len(brain.calls) == 2
    assert tag_list(store.jobs[2])[-1] == "circuit breaker"
    assert store.jobs[3]["step_log"] == []  # untouched, waits for a later sweep


def test_error_is_logged_and_halts():
    store, brain = FakeStore([job()]), FakeBrain(fail_on="Write it")
    kw.sweep_once(store, brain)
    last = tag_list(store.jobs[2])[-1]
    assert last.startswith("error: RuntimeError") and not kw.is_pending(store.jobs[2])


def test_legacy_draft_without_task_number_counts_for_first_task():
    tasks = [{"num": 1, "title": "Design", "depends_on": []}, {"num": 2, "title": "Wire", "depends_on": [1]}]
    agents = [{"name": "LUMEN", "role": "designer"}, {"name": "SUNDAY", "role": "builder"}]
    drafts = [{"agent": "LUMEN", "output": "# Layout"}]
    approvals = [{"from": "user", "to": "system", "type": "yes_no", "status": "yes"}]
    store, brain = FakeStore([job(tasks=tasks, agents=agents, drafts=drafts, approvals=approvals)]), FakeBrain()
    kw.sweep_once(store, brain)
    assert [c[0] for c in brain.calls] == ["SUNDAY"]
    assert tag_list(store.jobs[2])[-1] == "approval asked"
