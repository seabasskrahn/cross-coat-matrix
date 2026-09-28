"""The workflow: SUNDAY -> STEWARD/BEZEL -> specialist -> (approval if outward) -> done.

Every step follows the Keeper job envelope (matrix/envelope.py) and is written to the job's row
in the `jobs` table the moment it happens (matrix/keeper_store.py; memory if no database).
"""
import re
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import config, envelope as env
from .agents import ALWAYS_OUTWARD, DELEGATES, OUTWARD_WORDS, SENIOR_STAFF, SPECIALISTS
from .keeper_store import get_store
from .state import MatrixState
from . import llm


# ---------- writing to the job row ----------
def _save_append(state: MatrixState, column: str, item: dict) -> dict:
    if state.get("job_id") is not None:
        get_store().append(state["job_id"], column, item)
    return item


def step(state: MatrixState, tag: str, **detail) -> dict:
    """Make a step_log entry and save it to the job row right away."""
    return _save_append(state, "step_log", env.entry(tag, **detail))


def _save_field(state: MatrixState, column: str, value):
    if state.get("job_id") is not None:
        get_store().set_field(state["job_id"], column, value)
    return value


# ---------- Tier 1: SUNDAY (router) ----------
def sunday(state: MatrixState) -> dict:
    parts = [p.strip() for p in re.split(r"[\n;]+", state["message"]) if p.strip()] or [state["message"]]
    tasks = env.make_tasks(parts[: config.MAX_TASKS_PER_SWEEP])
    entries = []
    if len(parts) > config.MAX_TASKS_PER_SWEEP:
        entries.append(step(state, env.TASK_CAP, detail=f"{len(parts)} tasks received, only first "
                            f"{config.MAX_TASKS_PER_SWEEP} kept (max 5 per sweep)"))
    _save_field(state, "tasks", tasks)
    entries.append(step(state, env.TASK_CREATED, count=len(tasks), tasks=[t["num"] for t in tasks]))
    senior = llm.classify(state["message"], SENIOR_STAFF, "BEZEL",
                          "You are SUNDAY, the router. STEWARD = strategy/pricing/quality/takeoffs. "
                          "BEZEL = day-to-day operations, crew, truck, money admin.")
    entries.append(step(state, env.ROUTED, agent="SUNDAY", to=senior, source=state.get("source", "unknown")))
    return {"tasks": tasks, "senior": senior, "step_log": entries}


def pick_senior(state: MatrixState) -> str:
    return state["senior"]


# ---------- Tier 1: senior staff delegate to specialists ----------
def make_senior(name: str):
    def node(state: MatrixState) -> dict:
        table = {s: SPECIALISTS[s][1] for s in DELEGATES[name]}
        spec = llm.classify(state["message"], table, DELEGATES[name][0],
                            f"You are {name}, senior staff. Pick the best specialist: "
                            + "; ".join(f"{s} = {SPECIALISTS[s][0]}" for s in DELEGATES[name]))
        who = {"name": spec, "role": env.role_label(spec)}
        tasks = [{**t, "agent": dict(who)} for t in state.get("tasks", [])]
        _save_field(state, "tasks", tasks)
        agents = _save_field(state, "agents", [dict(who)])
        entries = [step(state, env.AGENT_ASSIGNED, task=t["num"], agent=spec, role=who["role"], via=name)
                   for t in tasks]
        return {"specialist": spec, "tasks": tasks, "agents": agents, "step_log": entries}
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
    def node(state: MatrixState) -> dict:
        tasks = state.get("tasks") or env.make_tasks([state["message"]])
        drafts, entries = [], []
        for idx in env.topo_order(tasks):  # dependency order; drafts only, nothing is sent
            task = tasks[idx]
            agent, role, _how = env.route(task, idx, state.get("agents"))
            mock = f"[{agent} mock] Handled task {task['num']}: {task.get('title', '')}"
            output = env.brain(agent, role, env.task_prompt(state["message"], task), mock)
            drafts.append(_save_append(state, "drafts", env.draft(task["num"], agent, role, output)))
            entries.append(step(state, env.DRAFT_SAVED, task=task["num"], agent=agent, role=role))
        kind = detect_outward(name, state["message"])
        action = {"kind": kind or "internal", "outward": bool(kind),
                  "summary": f"{name} wants to {kind.replace('_', ' ')}: {state['message'][:200]}" if kind
                  else "internal only (nothing leaves the system)"}
        if kind:
            entries.append(step(state, env.OUTWARD_ACTION, agent=name, kind=kind))
        return {"drafts": drafts, "proposed_action": action, "step_log": entries}
    node.__name__ = name.lower()
    return node


def needs_approval(state: MatrixState) -> str:
    return "ask_approval" if state["proposed_action"].get("outward") else "respond"


# ---------- Approvals: a thread entry, then wait for the owner ----------
def ask_approval(state: MatrixState) -> dict:
    """Add a pending yes/no exchange to the approvals thread. The job waits on it."""
    action = state["proposed_action"]
    item = _save_append(state, "approvals", env.approval(f"Approve? {action['summary']}", kind=action["kind"]))
    approvals = list(state.get("approvals") or []) + [item]
    e = step(state, env.APPROVAL_ASKED, approval=len(approvals) - 1, kind=action["kind"])
    return {"approvals": approvals, "step_log": [e]}


def approval_gate(state: MatrixState) -> dict:
    # Nothing before interrupt() may have side effects: this node re-runs from the top when resumed.
    approvals = list(state.get("approvals") or [])
    index = len(approvals) - 1
    decision = interrupt({
        "question": approvals[index]["text"] if approvals else f"Approve? {state['proposed_action']['summary']}",
        "action": state["proposed_action"],
        "draft": env.combined_output(state.get("drafts")),
        "approval": index,
    })
    approved = str((decision or {}).get("approved", "")).lower() in {"true", "yes", "y", "1"}
    who = (decision or {}).get("approver_id", "owner")
    changes = {"status": "yes" if approved else "no", "answered_by": who, "answered_at": env.utc_now()}
    if state.get("job_id") is not None:
        get_store().update_item(state["job_id"], "approvals", index, changes)
    approvals[index] = {**approvals[index], **changes}
    e = step(state, env.APPROVAL_ANSWERED, approval=index, status=changes["status"],
             type=env.exchange_tag(approvals[index]), by=who)
    return {"approvals": approvals, "step_log": [e]}


def execute(state: MatrixState) -> dict:
    action = state["proposed_action"]
    last = (state.get("approvals") or [{}])[-1]
    draft = env.combined_output(state.get("drafts"))
    if env.status_of(last) in env.YES:
        # TODO later: actually perform the action here (send, write to QBO, ...). Starter only logs it.
        reply = f"Approved. (Starter mode: would now {action['kind'].replace('_', ' ')}.)\n{draft}"
        entries = [step(state, env.NOT_WIRED_UP, detail=f"would perform {action['kind']} (not wired up yet)"),
                   step(state, env.JOB_CLOSED)]
        return {"reply": reply, "step_log": entries}
    entries = [step(state, env.REJECTED, detail="cancelled"), step(state, env.JOB_CLOSED)]
    return {"reply": "Cancelled - nothing was sent or changed.", "step_log": entries}


def respond(state: MatrixState) -> dict:
    return {"reply": env.combined_output(state.get("drafts")),
            "step_log": [step(state, env.JOB_CLOSED, detail="no approval needed (internal only)")]}


# ---------- Wire it all together ----------
def build_graph(checkpointer=None):
    g = StateGraph(MatrixState)
    g.add_node("sunday", sunday)
    for senior in DELEGATES:
        g.add_node(senior, make_senior(senior))
    for spec in SPECIALISTS:
        g.add_node(spec, make_specialist(spec))
    g.add_node("ask_approval", ask_approval)
    g.add_node("approval_gate", approval_gate)
    g.add_node("execute", execute)
    g.add_node("respond", respond)

    g.add_edge(START, "sunday")
    g.add_conditional_edges("sunday", pick_senior, list(DELEGATES))
    for senior, specs in DELEGATES.items():
        g.add_conditional_edges(senior, pick_specialist, specs)
    for spec in SPECIALISTS:
        g.add_conditional_edges(spec, needs_approval, ["ask_approval", "respond"])
    g.add_edge("ask_approval", "approval_gate")
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
