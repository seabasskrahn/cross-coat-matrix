"""SUNDAY's splitter: fewer, meatier tasks; one draft call per specialist; hard cap of 3."""
from langgraph.checkpoint.memory import InMemorySaver

from matrix import graph as g
from matrix import llm, runner, splitter


def agents_of(tasks):
    return [t.get("agent", {}).get("name") for t in tasks]


def test_single_message_is_one_task_left_for_senior():
    tasks, notes = splitter.split("Prep tomorrow's tailgate briefing for the crew, 7:30 am start")
    assert len(tasks) == 1 and "agent" not in tasks[0] and notes == []


def test_same_specialist_parts_merge():
    tasks, notes = splitter.split("What's my cash position; who still owes us; which ones are overdue")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "What's my cash position; who still owes us; which ones are overdue"
    assert notes == [{"tag": "tasks merged", "detail": "3 parts -> 1 task(s)"}]


def test_context_parts_join_previous():
    tasks, _ = splitter.split("Crew schedule for next week; make sure Abe brings the stilts; "
                              "it should include the site cleanup too")
    assert len(tasks) == 1  # no-keyword part and "it ..." part share the crew context


def test_different_specialists_split_with_agents():
    tasks, notes = splitter.split("Truck check engine light came on; book an oil change for the Silverado; "
                                  "log the fuel receipt from Friday")
    assert agents_of(tasks) == ["ARMOR", "DEDUCT"]
    assert tasks[0]["agent"]["role"] == "truck maintenance"
    assert [t["num"] for t in tasks] == [1, 2]
    assert notes[-1]["tag"] == "tasks merged"


def test_after_that_creates_dependency():
    tasks, _ = splitter.split("Walkthrough at the Penner job found deficiencies\n"
                              "make a handover PDF once they're fixed\nand send the final invoice after that")
    assert agents_of(tasks) == ["FINISH", "AUDIT"]
    assert tasks[1]["depends_on"] == [1] and tasks[0]["depends_on"] == []


def test_hard_cap_merges_by_senior_and_logs():
    msg = ("1. tailgate briefing for Monday\n2. punch list at Peters\n3. Silverado tire rotation\n"
           "4. CCA on the new compressor\n5. quote the Reimer reno\n6. invoice Dyck\n7. takeoff for Reimer")
    tasks, notes = splitter.split(msg)
    assert len(tasks) == 3
    assert [n["tag"] for n in notes] == ["tasks capped", "tasks merged"]
    # nothing dropped: every part is in some task
    for word in ("tailgate", "punch", "Silverado", "CCA", "quote", "invoice", "takeoff"):
        assert any(word in t["title"] for t in tasks)
    merged = [t for t in tasks if t.get("specialists")]
    assert merged and all(t["agent"]["name"] in ("STEWARD", "BEZEL") for t in merged)
    assert not any(t["title"].startswith("1.") for t in tasks)  # bullets stripped


def test_cap_is_configurable():
    tasks, _ = splitter.split("tailgate briefing; Silverado oil; quote the reno", cap=2)
    assert len(tasks) == 2


def test_ledger_always_stops_even_when_merged():
    task = {"num": 1, "agent": {"name": "BEZEL", "role": "senior staff"}, "specialists": ["LEDGER", "TAPER"]}
    assert g.detect_outward("TAPER", "tidy up the books and crew plan", [task]) == "write_quickbooks"


def test_batches_one_call_per_agent_respecting_dependencies():
    a = {"name": "TAPER", "role": "field ops"}
    b = {"name": "MARGIN", "role": "pricing"}
    tasks = [{"num": 1, "title": "x", "depends_on": [], "agent": a},
             {"num": 2, "title": "y", "depends_on": [], "agent": a},
             {"num": 3, "title": "z", "depends_on": [1], "agent": b}]
    assert [(ag, [t["num"] for t in batch]) for ag, _r, batch in g.batches(tasks, [])] == \
        [("TAPER", [1, 2]), ("MARGIN", [3])]
    # task 3 (TAPER) waits on MARGIN's task 2, so it can't ride along with task 1
    tasks = [{"num": 1, "depends_on": [], "agent": a}, {"num": 2, "depends_on": [1], "agent": b},
             {"num": 3, "depends_on": [2], "agent": a}]
    assert [[t["num"] for t in batch] for _a, _r, batch in g.batches(tasks, [])] == [[1], [2], [3]]


def test_batched_draft_records_covered_tasks(monkeypatch):
    calls = []
    real = llm.write
    monkeypatch.setattr(llm, "write", lambda role, text, mock: calls.append(text) or real(role, text, mock))
    state = {"message": "m", "agents": [],
             "tasks": [{"num": 1, "title": "x", "depends_on": [], "agent": {"name": "TAPER", "role": "field ops"}},
                       {"num": 2, "title": "y", "depends_on": [], "agent": {"name": "TAPER", "role": "field ops"}}]}
    out = g.make_specialist("TAPER")(state)
    assert len(calls) == 1 and calls[0] == "m"  # one call covering the whole job
    assert out["drafts"][0]["task"] == 1 and out["drafts"][0]["covers"] == [1, 2]
    assert out["step_log"][0]["covers"] == [1, 2]


def test_multi_specialist_message_end_to_end():
    calls = []
    real = llm.write
    llm.write = lambda role, text, mock: calls.append(role) or real(role, text, mock)
    try:
        graph = g.build_graph(InMemorySaver())
        r = runner.start(graph, "Tailgate briefing for the crew; Silverado oil change; quote the Reimer reno")
    finally:
        llm.write = real
    assert [t["agent"]["name"] for t in r["tasks"]] == ["TAPER", "ARMOR", "MARGIN"]
    assert [d["agent"] for d in r["drafts"]] == ["TAPER", "ARMOR", "MARGIN"] and len(calls) == 3
    assert {a["name"] for a in r["agents"]} == {"TAPER", "ARMOR", "MARGIN"}
    assert [e["tag"] for e in r["step_log"]].count("agent assigned") == 3
