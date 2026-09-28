"""Measure how many tasks and draft (brain) calls the Matrix makes per message. Offline only:
mock LLM, in-memory jobs + checkpoints, so it costs nothing. Run: python scripts/bench_splitter.py

It counts every llm.write call (one per draft). In real mode each message also makes 2 routing
calls (SUNDAY -> senior, senior -> specialist); those are the same before and after and not counted.
"""
import json
import os
import sys
from pathlib import Path

os.environ["LLM_PROVIDER"] = "mock"
os.environ["CHECKPOINT_DB_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from matrix import llm, runner  # noqa: E402
from matrix.graph import build_graph  # noqa: E402

# Realistic Cross Coat Drywall messages (several multi-part).
MESSAGES = [
    "Prep tomorrow's tailgate briefing for the crew at the Peters basement job, 7:30 am start",
    "Punch list for the Wiebe garage: corner bead dent by the door; tape seam cracking over the stairs; "
    "sand the patch in the hallway",
    "Send the Friesen invoice reminder, they still owe for the garage board and tape",
    "Quote the Klassen basement: 2400 sq ft of board, level 4 finish\nwhat margin should I use on materials?\n"
    "also do the takeoff from the blueprint they emailed",
    "Truck check engine light came on; book an oil change for the Silverado; log the fuel receipt from Friday",
    "Reconcile this week's bookkeeping in QuickBooks",
    "Crew schedule for next week: Mon-Tue at Peters, Wed at Dueck; make sure Abe brings the stilts; "
    "it should include the Thursday site cleanup too",
    "Walkthrough at the Penner job found three deficiencies\nmake a handover PDF once they're fixed\n"
    "and send the final invoice after that",
    "What's my cash position this month; who still owes us; which ones are overdue past 60 days",
    "1. tailgate briefing for Monday\n2. punch list at Peters\n3. Silverado tire rotation\n"
    "4. CCA on the new compressor\n5. quote the Reimer reno\n6. invoice Dyck for the garage\n7. takeoff for Reimer",
]


def main():
    calls = []
    real_write = llm.write
    llm.write = lambda role, text, mock: calls.append(role) or real_write(role, text, mock)
    graph = build_graph(InMemorySaver())
    rows = []
    for msg in MESSAGES:
        before = len(calls)
        r = runner.start(graph, msg, source="bench")
        tasks = graph.get_state({"configurable": {"thread_id": r["thread_id"]}}).values.get("tasks", [])
        parts = [p for p in msg.replace(";", "\n").split("\n") if p.strip()]
        rows.append({"parts": len(parts), "tasks": len(tasks), "draft_calls": len(calls) - before,
                     "status": r["status"], "message": msg.replace("\n", " / ")[:60]})
    llm.write = real_write
    for row in rows:
        print(f"{row['parts']:>2} parts  {row['tasks']:>2} tasks  {row['draft_calls']:>2} draft calls  "
              f"{row['status']:<15} {row['message']}")
    n = len(rows)
    summary = {k: {"avg": round(sum(r[k] for r in rows) / n, 2), "max": max(r[k] for r in rows),
                   "total": sum(r[k] for r in rows)} for k in ("tasks", "draft_calls")}
    print(json.dumps({"messages": n, **summary}))


if __name__ == "__main__":
    main()
