"""Small helpers to start a message and to resume it after the owner's yes/no."""
from langgraph.types import Command

from .graph import new_thread_id


def _summary(graph, thread_id: str) -> dict:
    snap = graph.get_state({"configurable": {"thread_id": thread_id}})
    values = snap.values
    out = {"thread_id": thread_id,
           "senior": values.get("senior"), "specialist": values.get("specialist"),
           "handoff_log": values.get("handoff_log", [])}
    if snap.interrupts:  # paused at the approval gate
        payload = snap.interrupts[0].value
        out.update(status="needs_approval", question=payload["question"],
                   action=payload["action"], draft=payload.get("draft", ""), reply=None)
    else:
        out.update(status="done", reply=values.get("reply"), approval=values.get("approval"))
    return out


def start(graph, text: str, source: str = "api", chat_id: str = "", thread_id: str | None = None) -> dict:
    thread_id = thread_id or new_thread_id()
    graph.invoke({"message": text, "source": source, "chat_id": chat_id, "handoff_log": []},
                 {"configurable": {"thread_id": thread_id}})
    return _summary(graph, thread_id)


def resume(graph, thread_id: str, approved: bool, approver_id: str = "owner") -> dict:
    cfg = {"configurable": {"thread_id": thread_id}}
    snap = graph.get_state(cfg)
    if not snap.interrupts:
        raise LookupError(f"Nothing is waiting for approval on thread {thread_id}")
    graph.invoke(Command(resume={"approved": approved, "approver_id": approver_id}), cfg)
    return _summary(graph, thread_id)
