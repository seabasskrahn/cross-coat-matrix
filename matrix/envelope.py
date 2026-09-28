"""The Keeper job envelope (docs/KEEPER_SPEC.md): six fields, their shapes, and the rules both the
main Matrix flow (graph.py) and the Keeper worker (keeper_worker.py) share.

    message    the original text the owner sent
    tasks      numbered list: {"num", "title", "depends_on": [nums], "agent": {"name", "role"}}
    agents     who is on the job: [{"name", "role"}]
    drafts     each agent's output, logged as-is: {"task", "agent", "role", "output", "at"}
    approvals  the dialogue thread: {"from", "to", "type": yes_no|context_request, "text",
               "status": pending|yes|no|answered, "at", ...}. Any "pending" entry = the job WAITS.
    step_log   chronological: {"tag", "at": ISO-8601 UTC, ...detail}. Starter tags below + freeform.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------- tags ----------
# Fixed starter tags (docs/KEEPER_SPEC.md).
MESSAGE_RECEIVED = "message received"
TASK_CREATED = "task created"
AGENT_ASSIGNED = "agent assigned"
DRAFT_SAVED = "draft saved"
APPROVAL_ASKED = "approval asked"
APPROVAL_ANSWERED = "approval answered"
JOB_CLOSED = "job closed"
STARTER_TAGS = (MESSAGE_RECEIVED, TASK_CREATED, AGENT_ASSIGNED, DRAFT_SAVED,
                APPROVAL_ASKED, APPROVAL_ANSWERED, JOB_CLOSED)
# Freeform tags used by the code ("anything unusual gets a freeform tag").
PICKED_UP = "picked up"
ROUTED = "routed"
TASK_CAP = "task cap"
OUTWARD_ACTION = "outward action"
NOT_WIRED_UP = "not wired up"
NOT_IN_ROSTER = "agent not in roster"
CYCLE = "dependency cycle"
BREAKER = "circuit breaker"
REJECTED = "rejected"
HALT_TAGS = (CYCLE, BREAKER)  # plus anything starting with "error"

# ---------- approval thread vocabulary ----------
YES_NO = "yes_no"
CONTEXT_REQUEST = "context_request"
PENDING, ANSWERED = "pending", "answered"
YES = {"yes", "y", "approved", "approve", "true", "ok"}
NO = {"no", "n", "rejected", "reject", "false", "denied"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry(tag: str, **detail) -> dict:
    """One step_log entry."""
    return {"tag": tag, "at": utc_now(), **detail}


def approval(text: str, *, frm: str = "system", to: str = "user", type: str = YES_NO, **extra) -> dict:
    """A new pending entry for the approvals thread."""
    return {"from": frm, "to": to, "type": type, "text": text, "status": PENDING, "at": utc_now(), **extra}


def draft(task: int, agent: str, role: str, output, **extra) -> dict:
    """A drafts entry. `output` is stored exactly as the agent produced it."""
    return {"task": task, "agent": agent, "role": role, "output": output, "at": utc_now(), **extra}


# ---------- reading a job ----------
def as_list(value) -> list:
    return value if isinstance(value, list) else []


def tags(job: dict) -> list[str]:
    return [str(e.get("tag", "")) for e in as_list(job.get("step_log")) if isinstance(e, dict)]


def is_halt_tag(tag: str) -> bool:
    return tag in HALT_TAGS or tag.lower().startswith("error")


def status_of(item: dict) -> str:
    return str(item.get("status", PENDING)).strip().lower()


def has_pending_approval(job: dict) -> bool:
    return any(isinstance(a, dict) and status_of(a) == PENDING for a in as_list(job.get("approvals")))


def is_halted(job: dict) -> bool:
    t = tags(job)
    return bool(t) and is_halt_tag(t[-1])


def is_pending(job: dict) -> bool:
    """Work to do? Not closed, not waiting on the owner, not halted for the owner to look at."""
    return JOB_CLOSED not in tags(job) and not has_pending_approval(job) and not is_halted(job)


def is_context_request(item: dict) -> bool:
    return "context" in str(item.get("type", "")).lower()


def exchange_tag(item: dict) -> str:
    """How the spec tags each exchange."""
    return "context request" if is_context_request(item) else "quick yes/no"


def approval_answer(item: dict) -> str:
    for key in ("answer", "reply", "response", "context"):
        if item.get(key):
            return str(item[key])
    return ""


# ---------- tasks ----------
class DependencyCycle(ValueError):
    pass


def task_num(task: dict, index: int) -> int:
    try:
        return int(task.get("num", index + 1))
    except (TypeError, ValueError):
        return index + 1


def make_tasks(titles: list[str]) -> list[dict]:
    """Numbered task list from plain titles (independent tasks, no dependencies)."""
    return [{"num": i + 1, "title": t, "depends_on": []} for i, t in enumerate(titles)]


def topo_order(tasks: list[dict]) -> list[int]:
    """Indexes of tasks in dependency order (stable: original order among ready tasks).
    Raises DependencyCycle for a cycle or a dependency on a task that doesn't exist."""
    nums = [task_num(t, i) for i, t in enumerate(tasks)]
    by_num = {n: i for i, n in enumerate(nums)}
    deps = {}
    for i, t in enumerate(tasks):
        d = set()
        for dep in as_list(t.get("depends_on")):
            try:
                dep = int(dep)
            except (TypeError, ValueError):
                raise DependencyCycle(f"task {nums[i]} has a bad dependency {dep!r}")
            if dep not in by_num:
                raise DependencyCycle(f"task {nums[i]} depends on missing task {dep}")
            d.add(by_num[dep])
        deps[i] = d
    done, order = set(), []
    while len(order) < len(tasks):
        ready = [i for i in range(len(tasks)) if i not in done and deps[i] <= done]
        if not ready:
            stuck = [nums[i] for i in range(len(tasks)) if i not in done]
            raise DependencyCycle(f"tasks {stuck} depend on each other")
        order.append(ready[0])
        done.add(ready[0])
    return order


# ---------- agents ----------
SENIOR = ("STEWARD", "senior staff")  # fallback route when a task has no agent


def route(task: dict, index: int, agents: list) -> tuple[str, str, str]:
    """(name, role, how). Explicit task agent > agents[index] > senior staff (STEWARD)."""
    agents = [a for a in as_list(agents) if isinstance(a, dict) and a.get("name")]
    explicit = task.get("agent")
    if isinstance(explicit, dict) and explicit.get("name"):
        return str(explicit["name"]), str(explicit.get("role") or task.get("role") or "agent"), "task"
    if isinstance(explicit, str) and explicit.strip():
        name = explicit.strip()
        role = task.get("role") or next((a.get("role") for a in agents if a["name"] == name), None)
        return name, str(role or "agent"), "task"
    if index < len(agents):
        a = agents[index]
        return str(a["name"]), str(a.get("role") or "agent"), "index"
    return SENIOR[0], SENIOR[1], "senior fallback"


def roster() -> dict[str, str]:
    """Agents defined in matrix/agents.py: {name: job description}."""
    from . import agents, names
    known = {names.ROUTER_ID: "router: sends each message to the right senior staff"}
    known.update({n: names._SENIOR_ROLES.get(n, "senior staff") for n in agents.SENIOR_STAFF})
    known.update({n: job for n, (job, _kw) in agents.SPECIALISTS.items()})
    return known


def role_label(name: str) -> str:
    """Short role label for an agent in agents.py (e.g. TAPER -> "field ops")."""
    from . import agents
    return agents.ROLE_LABELS.get(name, "agent")


def task_prompt(message: str, task: dict, extra: str = "") -> str:
    title = str(task.get("title", ""))
    if title.strip() == str(message).strip():
        return title + extra
    return f"Job: {message}\nYour task #{task_num(task, 0)}: {title}{extra}"


def brain(name: str, role: str, text: str, mock: str | None = None) -> str:
    """Have an agent draft (never act). Reuses llm.write, the same brain the specialists always used.
    Agents not in agents.py get a prompt built from their name and role."""
    from . import llm
    known = roster()
    who = f"{name} ({known[name]}; role on this job: {role})" if name in known else f"{name}, the team's {role}"
    if mock is None:
        mock = f"[{name} mock] Draft ({role}): {text.splitlines()[-1] if text else ''}"
    return llm.write(who, text, mock)


def combined_output(drafts: list[dict]) -> str:
    """All draft outputs as one reply (single draft = its output unchanged)."""
    drafts = [d for d in as_list(drafts) if isinstance(d, dict)]
    if len(drafts) == 1:
        return str(drafts[0].get("output", ""))
    return "\n\n".join(f"{d.get('task')}. {d.get('output', '')}" for d in drafts)


# ---------- display ----------
def describe(e: dict, tz: str = "America/Edmonton") -> str:
    """One step_log entry as a readable line in local time, e.g. '7:09 PM agent assigned: TAPER (field ops)'."""
    try:
        at = datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        when = at.astimezone(ZoneInfo(tz)).strftime("%I:%M %p").lstrip("0")
    except (KeyError, ValueError, TypeError):
        when = "?"
    bits = []
    if e.get("agent"):
        bits.append(f"{e['agent']} ({e['role']})" if e.get("role") else str(e["agent"]))
    if e.get("task") is not None:
        bits.append(f"task {e['task']}")
    for key in ("to", "status", "by", "kind", "detail", "source"):
        if e.get(key) not in (None, ""):
            bits.append(f"{key} {e[key]}" if key in ("to", "status", "by", "source") else str(e[key]))
    return f"{when} {e.get('tag', '')}" + (": " + ", ".join(bits) if bits else "")
