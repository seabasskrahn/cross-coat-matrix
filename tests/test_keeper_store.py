"""Keeper store + main Matrix flow writing the six-field envelope to the jobs table (memory/mocked DB)."""
import pytest

from matrix import envelope as env
from matrix import keeper_store, runner
from matrix import keeper_worker as kw
from matrix.graph import build_graph
from matrix.keeper_store import MemoryStore


@pytest.fixture
def store():
    s = MemoryStore()
    keeper_store.set_store(s)
    yield s
    keeper_store.set_store(None)


@pytest.fixture(scope="module")
def graph():
    from langgraph.checkpoint.memory import InMemorySaver
    return build_graph(InMemorySaver())


def tag_list(row):
    return [e["tag"] for e in row["step_log"]]


def test_no_db_url_falls_back_to_memory():
    keeper_store.set_store(None)
    try:
        assert isinstance(keeper_store.get_store(), MemoryStore)
    finally:
        keeper_store.set_store(None)


def test_memory_store_crud():
    s = MemoryStore()
    row = s.create("hello", step_log=[env.entry(env.MESSAGE_RECEIVED)])
    assert row["id"] == 1 and s.get(1)["message"] == "hello"
    s.append(1, "approvals", env.approval("ok?"))
    s.update_item(1, "approvals", 0, {"status": "yes"})
    s.set_field(1, "tasks", env.make_tasks(["a", "b"]))
    got = s.get(1)
    assert got["approvals"][0]["status"] == "yes" and [t["num"] for t in got["tasks"]] == [1, 2]
    assert s.get(99) is None


def test_internal_message_becomes_closed_job_row(store, graph):
    r = runner.start(graph, "Tailgate briefing for the crew tomorrow 7:30 am", source="telegram")
    row = store.rows[r["job_id"]]
    assert row["message"] == "Tailgate briefing for the crew tomorrow 7:30 am"
    assert row["tasks"] == [{"num": 1, "title": row["message"], "depends_on": [],
                             "agent": {"name": "TAPER", "role": "field ops"}}]
    assert row["agents"] == [{"name": "TAPER", "role": "field ops"}]
    d = row["drafts"][0]
    assert (d["task"], d["agent"], d["role"]) == (1, "TAPER", "field ops") and d["at"].endswith("Z")
    assert row["approvals"] == []
    assert tag_list(row) == ["message received", "task created", "routed", "agent assigned",
                             "draft saved", "job closed"]
    assert row["step_log"] == r["step_log"]  # what the graph saw is exactly what was saved
    assert r["job_id"] not in store.locked  # released when the run ends


def test_outward_message_waits_on_thread_then_closes(store, graph):
    r = runner.start(graph, "Send the Friesen invoice reminder, they owe us")
    row = store.rows[r["job_id"]]
    assert r["status"] == "needs_approval"
    assert row["approvals"][-1]["status"] == "pending" and row["approvals"][-1]["type"] == "yes_no"
    assert tag_list(row)[-2:] == ["outward action", "approval asked"]
    # while waiting, the worker leaves it alone
    kw.sweep_once(store, lambda *a: pytest.fail("worker must not draft a waiting job"))
    assert tag_list(store.rows[r["job_id"]])[-1] == "approval asked"
    done = runner.resume(graph, r["thread_id"], True, "owner")
    row = store.rows[r["job_id"]]
    assert row["approvals"][-1]["status"] == "yes" and row["approvals"][-1]["answered_by"] == "owner"
    assert tag_list(row)[-3:] == ["approval answered", "not wired up", "job closed"]
    assert done["approvals"][-1]["status"] == "yes"


def test_rejection_logs_rejected(store, graph):
    r = runner.start(graph, "Delete the old Wiebe quote")
    runner.resume(graph, r["thread_id"], False)
    assert tag_list(store.rows[r["job_id"]])[-3:] == ["approval answered", "rejected", "job closed"]


def test_multi_task_message_numbered_and_each_drafted(store, graph):
    r = runner.start(graph, "tailgate briefing for the crew; Silverado oil change; quote the Reimer reno")
    row = store.rows[r["job_id"]]
    assert [t["num"] for t in row["tasks"]] == [1, 2, 3]
    assert [(d["task"], d["agent"]) for d in row["drafts"]] == [(1, "TAPER"), (2, "ARMOR"), (3, "MARGIN")]
    assert tag_list(row).count("agent assigned") == 3 and tag_list(row).count("draft saved") == 3


def test_related_parts_merge_into_one_saved_task(store, graph):
    r = runner.start(graph, "punch item 1; punch item 2; punch item 3")
    row = store.rows[r["job_id"]]
    assert len(row["tasks"]) == 1 and len(row["drafts"]) == 1
    assert "tasks merged" in tag_list(row)


def test_graph_error_is_logged_and_halts(store, graph, monkeypatch):
    from matrix import envelope
    monkeypatch.setattr(envelope, "brain", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        runner.start(graph, "Tailgate briefing for the crew")
    row = store.rows[max(store.rows)]
    assert tag_list(row)[-1].startswith("error: RuntimeError") and not env.is_pending(row)


# ---------- PgStore with a mocked connection ----------
class FakeConn:
    def __init__(self, tables):
        self.tables, self.queries = tables, []

    def execute(self, query, params=()):
        self.queries.append((query, params))
        tables = self.tables
        return type("Cur", (), {"fetchall": lambda _s: [{"relname": t} for t in tables]})()


def pg_with(tables):
    s = keeper_store.PgStore.__new__(keeper_store.PgStore)
    s.conn = FakeConn(tables)
    return s


def test_pgstore_prefers_lowercase_jobs():
    assert pg_with(["Jobs", "jobs"]).detect_table() == "jobs"
    assert pg_with(["Jobs"]).detect_table() == "Jobs"
    with pytest.raises(RuntimeError):
        pg_with([]).detect_table()


def test_describe_step_entry():
    line = env.describe({"tag": "agent assigned", "at": "2026-09-28T01:09:14Z", "agent": "TAPER",
                         "role": "field ops", "task": 1}, "America/Edmonton")
    assert line == "7:09 PM agent assigned: TAPER (field ops), task 1"
