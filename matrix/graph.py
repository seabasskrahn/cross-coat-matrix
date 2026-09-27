"""The workflow: SUNDAY -> STEWARD/BEZEL -> specialist -> (approval gate if outward) -> done."""
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import config, llm
from .agents import ALWAYS_OUTWARD, DELEGATES, OUTWARD_WORDS, SENIOR_STAFF, SPECIALISTS
from .state import MatrixState


def now() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%I:%M %p").lstrip("0")


def log(who: str, what: str) -> list[str]:
    return [f"{now()} {who}: {what}"]


# ---------- Tier 1: SUNDAY (router) ----------
def sunday(state: MatrixState) -> dict:
    parts = [p.strip() for p in re.split(r"[\n;]+", state["message"]) if p.strip()]
    tasks = parts[: config.MAX_TASKS_PER_SWEEP]
    entries = []
    if len(parts) > config.MAX_TASKS_PER_SWEEP:
        entries += log("SUNDAY", f"{len(parts)} tasks received, only first {config.MAX_TASKS_PER_SWEEP} kept (max 5 per sweep)")
    senior = llm.classify(state["message"], SENIOR_STAFF, "BEZEL",
                          "You are SUNDAY, the router. STEWARD = strategy/pricing/quality/takeoffs. "
                          "BEZEL = day-to-day operations, crew, truck, money admin.")
    entries += log("SUNDAY", f"routed to {senior} (from {state.get('source', 'unknown')})")
    return {"tasks": tasks, "senior": senior, "handoff_log": entries}


def pick_senior(state: MatrixState) -> str:
    return state["senior"]


# ---------- Tier 1: senior staff delegate to specialists ----------
def make_senior(name: str):
    def node(state: MatrixState) -> dict:
        table = {s: SPECIALISTS[s][1] for s in DELEGATES[name]}
        spec = llm.classify(state["message"], table, DELEGATES[name][0],
                            f"You are {name}, senior staff. Pick the best specialist: "
                            + "; ".join(f"{s} = {SPECIALISTS[s][0]}" for s in DELEGATES[name]))
        return {"specialist": spec, "handoff_log": log(name, f"delegated to {spec}")}
    node.__name__ = name.lower()
    return node


def pick_specialist(state: MatrixState) -> str:
    return state["specialist"]


# ---------- Tiers 2-4: specialist stubs ----------
def detect_outward(name: str, text: str) -> str | None:
    if name in ALWAYS_OUTWARD:
        return ALWAYS_OUTWARD[name]
    low = text.lower() + " "
    for kind, words in OUTWARD_WORDS.items():
        if any(w in low for w in words):
            return kind
    return None


def make_specialist(name: str):
    job = SPECIALISTS[name][0]

    def node(state: MatrixState) -> dict:
        tasks = state.get("tasks") or [state["message"]]
        mock = f"[{name} mock] Handled {len(tasks)} task(s): " + " | ".join(tasks)
        draft = llm.write(f"{name} ({job})", "\n".join(tasks), mock)
        kind = detect_outward(name, state["message"])
        action = {"kind": kind or "internal", "outward": bool(kind),
                  "summary": f"{name} wants to {kind.replace('_', ' ')}: {state['message'][:200]}" if kind
                  else "internal only (nothing leaves the system)"}
        return {"draft": draft, "proposed_action": action,
                "handoff_log": log(name, f"drafted work; outward action = {kind or 'none'}")}
    node.__name__ = name.lower()
    return node


def needs_approval(state: MatrixState) -> str:
    return "approval_gate" if state["proposed_action"].get("outward") else "respond"


# ---------- Approval gate: the owner says yes or no (via Telegram) ----------
def approval_gate(state: MatrixState) -> dict:
    # Nothing before interrupt() may have side effects: this node re-runs from the top when resumed.
    decision = interrupt({
        "question": f"Approve? {state['proposed_action']['summary']}",
        "action": state["proposed_action"],
        "draft": state.get("draft", ""),
    })
    approved = str((decision or {}).get("approved", "")).lower() in {"true", "yes", "y", "1"}
    who = (decision or {}).get("approver_id", "owner")
    return {"approval": "approved" if approved else "rejected",
            "handoff_log": log("APPROVAL", f"{'APPROVED' if approved else 'REJECTED'} by {who}")}


def execute(state: MatrixState) -> dict:
    action = state["proposed_action"]
    if state.get("approval") == "approved":
        # TODO later: actually perform the action here (send, write to QBO, ...). Starter only logs it.
        reply = f"Approved. (Starter mode: would now {action['kind'].replace('_', ' ')}.)\n{state.get('draft', '')}"
        return {"reply": reply, "handoff_log": log("EXECUTE", f"would perform {action['kind']} (not wired up yet)")}
    return {"reply": "Cancelled - nothing was sent or changed.", "handoff_log": log("EXECUTE", "cancelled")}


def respond(state: MatrixState) -> dict:
    return {"approval": "not_needed", "reply": state.get("draft", ""),
            "handoff_log": log("SUNDAY", "replied (no approval needed)")}


# ---------- Wire it all together ----------
def build_graph(checkpointer=None):
    g = StateGraph(MatrixState)
    g.add_node("sunday", sunday)
    for senior in DELEGATES:
        g.add_node(senior, make_senior(senior))
    for spec in SPECIALISTS:
        g.add_node(spec, make_specialist(spec))
    g.add_node("approval_gate", approval_gate)
    g.add_node("execute", execute)
    g.add_node("respond", respond)

    g.add_edge(START, "sunday")
    g.add_conditional_edges("sunday", pick_senior, list(DELEGATES))
    for senior, specs in DELEGATES.items():
        g.add_conditional_edges(senior, pick_specialist, specs)
    for spec in SPECIALISTS:
        g.add_conditional_edges(spec, needs_approval, ["approval_gate", "respond"])
    g.add_edge("approval_gate", "execute")
    g.add_edge("execute", END)
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer or make_checkpointer())


def make_checkpointer():
    """Postgres if CHECKPOINT_DB_URL is set (survives restarts), otherwise memory."""
    if config.CHECKPOINT_DB_URL:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row
        conn = Connection.connect(config.CHECKPOINT_DB_URL, autocommit=True,
                                  prepare_threshold=0, row_factory=dict_row)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    return InMemorySaver()


def new_thread_id() -> str:
    return uuid.uuid4().hex[:16]  # short, so it fits in Telegram button data (64-byte limit)
