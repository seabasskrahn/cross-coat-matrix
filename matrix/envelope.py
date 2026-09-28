"""The Keeper job envelope (docs/KEEPER_SPEC.md): six fields, their shapes, and the rules both the
main Matrix flow (graph.py) and the Keeper worker (keeper_worker.py) share.

    message    the original text the owner sent
    tasks      numbered list: {"num", "title", "depends_on": [nums], "agent": {"name", "role"}}
    agents     who is on the job: [{"name", "role"}]
    drafts     each agent's output, logged as-is: {"task", "agent", "role", "output", "at"}
    approvals  the dialogue thread: {"from", "to", "type": yes_no|context_request, "text",
               "status": pending|yes|no|answered, "at", ...}. Any "pending" entry = the job WAITS.
    step_log   chronological: {"tag", "at": ISO-8601 UTC, ...detail}. Starter tags below + freeform.

Undo / reopen: an owner's answer is only acted on once ANSWER_GRACE_SECONDS (config, default 30)
have passed since its "answered_at". An `approval reopened` step (approval=i) puts approval i back
to pending: it cancels an earlier `approval answered` for i, and a `job closed` before it no longer
counts (the job is open again, waiting on that approval).

Pause: a `paused` step (with no `resumed` after it) makes the worker skip the job. `paused` and
`resumed` steps are ignored when deciding whether a job is halted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
TASKS_MERGED = "tasks merged"
TASKS_CAPPED = "tasks capped"
OUTWARD_ACTION = "outward action"
NOT_WIRED_UP = "not wired up"
NOT_IN_ROSTER = "agent not in roster"
CYCLE = "dependency cycle"
BREAKER = "circuit breaker"
REJECTED = "rejected"
APPROVAL_REOPENED = "approval reopened"   # undo / reopen / change of an answered approval
PAUSED, RESUMED = "paused", "resumed"      # per-job On/Off switch (dashboard); the worker skips paused jobs
SCOUT_SEARCH = "scout search"              # a read-only web search ran for a task (query only, never a key)
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
    t = [x for x in tags(job) if x not in (PAUSED, RESUMED)]
    return bool(t) and is_halt_tag(t[-1])


def steps(job: dict) -> list[dict]:
    return [e for e in as_list(job.get("step_log")) if isinstance(e, dict)]


def _approval_index(e: dict):
    i = e.get("approval")
    return i if isinstance(i, int) and not isinstance(i, bool) else None


def is_paused(job: dict) -> bool:
    """Paused = a `paused` step with no `resumed` step after it."""
    paused = False
    for e in steps(job):
        if e.get("tag") == PAUSED:
            paused = True
        elif e.get("tag") == RESUMED:
            paused = False
    return paused


def is_closed(job: dict) -> bool:
    """Closed = a `job closed` step with no `approval reopened` step after it."""
    closed = False
    for e in steps(job):
        if e.get("tag") == JOB_CLOSED:
            closed = True
        elif e.get("tag") == APPROVAL_REOPENED:
            closed = False
    return closed


def acted_approvals(job: dict) -> set[int]:
    """Indexes of approvals whose answer has been acted on (`approval answered` logged) and not
    reopened since."""
    acted: set[int] = set()
    for e in steps(job):
        i = _approval_index(e)
        if i is None:
            continue
        if e.get("tag") == APPROVAL_ANSWERED:
            acted.add(i)
        elif e.get("tag") == APPROVAL_REOPENED:
            acted.discard(i)
    return acted


def unacted_answers(job: dict) -> list[int]:
    """Answered approvals nobody has acted on yet (these can still be undone)."""
    acted = acted_approvals(job)
    return [i for i, a in enumerate(as_list(job.get("approvals")))
            if isinstance(a, dict) and status_of(a) != PENDING and i not in acted]


def parse_utc(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def grace_seconds() -> float:
    from . import config
    return max(0.0, float(getattr(config, "ANSWER_GRACE_SECONDS", 30)))


def answer_deadline(item: dict, grace: float | None = None) -> datetime | None:
    """When the worker may act on this answer (answered_at + grace). None = no answered_at, so no wait."""
    at = parse_utc(item.get("answered_at")) if isinstance(item, dict) and item.get("answered_at") else None
    return at + timedelta(seconds=grace_seconds() if grace is None else grace) if at else None


def in_grace(job: dict, now: datetime | None = None, grace: float | None = None) -> bool:
    """True while any not-yet-acted answer is still inside its grace window."""
    now = now or datetime.now(timezone.utc)
    approvals = as_list(job.get("approvals"))
    for i in unacted_answers(job):
        deadline = answer_deadline(approvals[i], grace)
        if deadline is not None and now < deadline:
            return True
    return False


def reopenable_approval(job: dict) -> int | None:
    """If the job is closed BECAUSE an approval was rejected, that approval's index, else None."""
    if not is_closed(job):
        return None
    log = steps(job)
    last_close = max(k for k, e in enumerate(log) if e.get("tag") == JOB_CLOSED)
    if last_close == 0 or log[last_close - 1].get("tag") != REJECTED:
        return None
    index = _approval_index(log[last_close - 1])
    if index is None:  # e.g. the main flow logs `rejected` without an index: use the last answer
        answered = [e for e in log[:last_close] if e.get("tag") == APPROVAL_ANSWERED and _approval_index(e) is not None]
        index = _approval_index(answered[-1]) if answered else None
    approvals = as_list(job.get("approvals"))
    if index is None or not 0 <= index < len(approvals) or not isinstance(approvals[index], dict):
        return None
    return index if status_of(approvals[index]) in NO else None


def closing_answer(job: dict) -> tuple[int, str] | None:
    """If the job is closed because of the owner's last answer: (approval index, "no" or "yes").
    "no" = closed by a Reject; "yes" = the owner approved and the job closed right after."""
    rejected = reopenable_approval(job)
    if rejected is not None:
        return rejected, "no"
    if not is_closed(job):
        return None
    log = steps(job)
    last_close = max(k for k, e in enumerate(log) if e.get("tag") == JOB_CLOSED)
    for e in reversed(log[:last_close]):
        tag = e.get("tag")
        if tag in (REJECTED, APPROVAL_ASKED, APPROVAL_REOPENED, JOB_CLOSED):
            return None
        if tag == APPROVAL_ANSWERED:
            i = _approval_index(e)
            approvals = as_list(job.get("approvals"))
            if i is not None and 0 <= i < len(approvals) and isinstance(approvals[i], dict) \
                    and status_of(approvals[i]) in YES:
                return i, "yes"
            return None
    return None


def reopened(item: dict, now: str | None = None) -> dict:
    """An approval entry put back to pending (the answer removed)."""
    new = {k: v for k, v in item.items() if k not in ("answered_at", "answered_by", "answer")}
    new.update(status=PENDING, reopened_at=now or utc_now())
    return new


def is_pending(job: dict, now: datetime | None = None) -> bool:
    """Work to do? Not closed, not waiting on the owner, not halted for the owner to look at, not
    paused, and no fresh answer still inside its grace window (the owner may undo it)."""
    return (not is_closed(job) and not has_pending_approval(job) and not is_halted(job)
            and not is_paused(job) and not in_grace(job, now))


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
    return llm.write(who, text, mock, agent=name)  # each agent drafts with its own brain (llm.brain_for)


def batch_prompt(message: str, batch: list[dict], total_tasks: int, extra: str = "") -> str:
    """Prompt for one draft call covering one or more tasks for the same agent."""
    if len(batch) == total_tasks:
        return f"{message}{extra}"  # this call covers the whole job: just give it the message
    if len(batch) == 1:
        return task_prompt(message, batch[0], extra)
    lines = "\n".join(f"#{task_num(t, 0)}: {t.get('title', '')}" for t in batch)
    return f"Job: {message}\nYour tasks (answer each, numbered):\n{lines}{extra}"


def draft_task_nums(d: dict) -> list:
    """Task numbers a draft covers (a batched draft lists them in "covers")."""
    return list(d.get("covers") or [d.get("task")])


def combined_output(drafts: list[dict]) -> str:
    """All draft outputs as one reply (single draft = its output unchanged)."""
    drafts = [d for d in as_list(drafts) if isinstance(d, dict)]
    if len(drafts) == 1:
        return str(drafts[0].get("output", ""))
    return "\n\n".join(f"{', '.join(map(str, draft_task_nums(d)))}. {d.get('output', '')}" for d in drafts)


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
    for key in ("to", "status", "by", "kind", "detail", "source", "query"):
        if e.get(key) not in (None, ""):
            bits.append(f"{key} {e[key]}" if key in ("to", "status", "by", "source", "query") else str(e[key]))
    return f"{when} {e.get('tag', '')}" + (": " + ", ".join(bits) if bits else "")
