"""Smoke test: runs fully offline in mock mode. Run with: pytest"""
import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ["MATRIX_API_KEY"] = ""
os.environ["OWNER_TELEGRAM_ID"] = ""
os.environ["CHECKPOINT_DB_URL"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from matrix.api import app  # noqa: E402

client = TestClient(app)


def test_health():
    assert client.get("/health").json()["ok"] is True


def test_internal_message_needs_no_approval():
    r = client.post("/inbound", json={"text": "Tailgate briefing for the crew tomorrow 7:30 am", "source": "telegram"}).json()
    assert r["status"] == "done"
    assert r["senior"] == "BEZEL" and r["specialist"] == "TAPER"
    assert r["approvals"] == []  # nothing to approve
    assert r["step_log"][-1]["tag"] == "job closed"


def test_outward_message_stops_at_gate_then_approved():
    r = client.post("/inbound", json={"text": "Send the Friesen invoice reminder, they owe us"}).json()
    assert r["status"] == "needs_approval"
    assert r["specialist"] == "AUDIT"
    assert r["reply"] is None  # nothing happened yet
    done = client.post("/approve", json={"thread_id": r["thread_id"], "decision": "yes", "approver_id": "owner"}).json()
    assert done["status"] == "done" and done["approvals"][-1]["status"] == "yes"
    assert any(e["tag"] == "approval answered" and e["status"] == "yes" for e in done["step_log"])


def test_rejection_cancels():
    r = client.post("/inbound", json={"text": "Delete the old Wiebe quote"}).json()
    assert r["status"] == "needs_approval"
    done = client.post("/approve", json={"thread_id": r["thread_id"], "decision": "no"}).json()
    assert done["approvals"][-1]["status"] == "no" and "nothing was sent" in done["reply"]


def test_steward_route_and_ledger_always_gated():
    r = client.post("/inbound", json={"text": "What sq ft rate should I quote for the new bid?"}).json()
    assert r["senior"] == "STEWARD" and r["specialist"] == "MARGIN"
    r2 = client.post("/inbound", json={"text": "Reconcile this week's bookkeeping in QuickBooks"}).json()
    assert r2["specialist"] == "LEDGER" and r2["status"] == "needs_approval"


def test_same_specialist_parts_become_one_task():
    text = "; ".join(f"punch item {i}" for i in range(1, 9))
    r = client.post("/inbound", json={"text": text}).json()
    assert any(e["tag"] == "tasks merged" and e["detail"] == "8 parts -> 1 task(s)" for e in r["step_log"])
    assert len(r["tasks"]) == 1 and len(r["drafts"]) == 1
    assert all(f"punch item {i}" in r["tasks"][0]["title"] for i in range(1, 9))  # nothing dropped


def test_max_three_tasks_per_message():
    text = ("tailgate briefing; Silverado tire rotation; CCA on the compressor; quote the reno; "
            "invoice Dyck; takeoff for Reimer")
    r = client.post("/inbound", json={"text": text}).json()
    assert len(r["tasks"]) <= 3 and len(r["drafts"]) <= 3
    assert any(e["tag"] == "tasks capped" for e in r["step_log"])


def test_approve_unknown_thread_404():
    assert client.post("/approve", json={"thread_id": "nope", "decision": "yes"}).status_code == 404
