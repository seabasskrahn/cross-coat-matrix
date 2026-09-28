"""Small helpers to start a message and to resume it after the owner's yes/no.

Each message becomes a row in the Keeper `jobs` table (matrix/keeper_store.py). The row is locked
while the graph works on it, so the Keeper worker never picks up a job that is mid-flight.
"""
from langgraph.types import Command

from . import envelope as env
from .graph import new_thread_id
from .keeper_store import get_store


def _summary(graph, thread_id: str) -> dict:
    snap = graph.get_state({"configurable": {"thread_id": thread_id}})
    values = snap.values
    out = {"thread_id": thread_id, "job_id": values.get("job_id"),
           "senior": values.get("senior"), "specialist": values.get("specialist"),
           "message": values.get("message"), "tasks": values.get("tasks", []),
           "agents": values.get("agents", []), "drafts": values.get("drafts", []),
           "approvals": values.get("approvals", []), "step_log": values.get("step_log", []),
           "draft": env.combined_output(values.get("drafts"))}
    if snap.interrupts:  # waiting on the approvals thread
        payload = snap.interrupts[0].value
        out.update(status="needs_approval", question=payload["question"],
                   action=payload["action"], reply=None)
    else:
        out.update(status="done", reply=values.get("reply"))
    return out


def start(graph, text: str, source: str = "api", chat_id: str = "", thread_id: str | None = None) -> dict:
    thread_id = thread_id or new_thread_id()
    store = get_store()
    first = env.entry(env.MESSAGE_RECEIVED, source=source, thread_id=thread_id)
    job = store.create(text, lock=True, step_log=[first])
    try:
        graph.invoke({"job_id": job["id"], "message": text, "source": source, "chat_id": chat_id,
                      "tasks": [], "agents": [], "drafts": [], "approvals": [], "step_log": [first]},
                     {"configurable": {"thread_id": thread_id}})
    except Exception as exc:
        store.append(job["id"], "step_log", env.entry(f"error: {type(exc).__name__}: {str(exc)[:200]}"))
        raise
    finally:
        store.release(job["id"])
    return _summary(graph, thread_id)


def resume(graph, thread_id: str, approved: bool, approver_id: str = "owner") -> dict:
    cfg = {"configurable": {"thread_id": thread_id}}
    snap = graph.get_state(cfg)
    if not snap.interrupts:
        raise LookupError(f"Nothing is waiting for approval on thread {thread_id}")
    store, job_id = get_store(), snap.values.get("job_id")
    if job_id is not None:
        store.lock(job_id)
    try:
        graph.invoke(Command(resume={"approved": approved, "approver_id": approver_id}), cfg)
    finally:
        if job_id is not None:
            store.release(job_id)
    return _summary(graph, thread_id)
