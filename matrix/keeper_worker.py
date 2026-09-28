"""Keeper worker: picks up jobs saved in Postgres (docs/KEEPER_SPEC.md) and has agents draft them.

What it does, every POLL seconds (default 10, env KEEPER_POLL_SECONDS):
  1. Finds pending jobs: no `job closed` tag, no approval with status "pending" (those WAIT),
     and not halted (last step_log tag is `circuit breaker`, `dependency cycle` or `error: ...`).
  2. Claims one safely (SELECT ... FOR UPDATE SKIP LOCKED + a session advisory lock), logs `picked up`.
  3. Runs its tasks in dependency order. Each agent writes a DRAFT ONLY (never sends, pays,
     deletes or acts in the real world), saved as-is to `drafts`.
  4. Asks the owner "Approve these drafts?" in `approvals` and stops. It never approves itself.
     When the owner answers yes/no, the next poll logs `approval answered` and `job closed`.

Every change is written to the database immediately (one small UPDATE each), so a crash loses nothing.
A halted job is left alone for the owner; to let the worker try again, append any step_log entry
after the halt tag (for example {"tag": "resume", "at": "..."}).

Run:   python -m matrix.keeper_worker           (loop forever)
       python -m matrix.keeper_worker --once    (one sweep, then exit)
Stop:  Stop-Process -Id <pid>   (the PID is written to logs/keeper_worker.pid)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config

# ---------- settings ----------
POLL_SECONDS = float(os.getenv("KEEPER_POLL_SECONDS", "10"))
MAX_TASKS_PER_JOB_RUN = int(os.getenv("KEEPER_MAX_TASKS_PER_JOB", str(config.MAX_TASKS_PER_SWEEP)))  # 5
MAX_LLM_CALLS_PER_SWEEP = int(os.getenv("KEEPER_MAX_LLM_CALLS_PER_SWEEP", "5"))
TABLE_CANDIDATES = ("jobs", "Jobs")  # lowercase wins if both exist
LOCK_NAMESPACE = 4242                # first key of pg_try_advisory_lock(ns, job_id)
SENIOR = ("STEWARD", "senior")       # fallback route when a task has no agent

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"

# Fixed starter tags (docs/KEEPER_SPEC.md). Anything else is a freeform tag.
MESSAGE_RECEIVED = "message received"
TASK_CREATED = "task created"
AGENT_ASSIGNED = "agent assigned"
DRAFT_SAVED = "draft saved"
APPROVAL_ASKED = "approval asked"
APPROVAL_ANSWERED = "approval answered"
JOB_CLOSED = "job closed"
# Freeform tags used by the worker.
PICKED_UP = "picked up"
NOT_IN_ROSTER = "agent not in roster"
CYCLE = "dependency cycle"
BREAKER = "circuit breaker"
REJECTED = "rejected"
HALT_TAGS = (CYCLE, BREAKER)  # plus anything starting with "error"

YES = {"yes", "y", "approved", "approve", "true", "ok"}
NO = {"no", "n", "rejected", "reject", "false", "denied"}

log = logging.getLogger("keeper_worker")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry(tag: str, **detail) -> dict:
    return {"tag": tag, "at": utc_now(), **detail}


# ---------- pure decision helpers (unit-tested) ----------
def _list(value) -> list:
    return value if isinstance(value, list) else []


def tags(job: dict) -> list[str]:
    return [str(e.get("tag", "")) for e in _list(job.get("step_log")) if isinstance(e, dict)]


def is_halt_tag(tag: str) -> bool:
    return tag in HALT_TAGS or tag.lower().startswith("error")


def status_of(approval: dict) -> str:
    return str(approval.get("status", "pending")).strip().lower()


def has_pending_approval(job: dict) -> bool:
    return any(isinstance(a, dict) and status_of(a) == "pending" for a in _list(job.get("approvals")))


def is_halted(job: dict) -> bool:
    t = tags(job)
    return bool(t) and is_halt_tag(t[-1])


def is_pending(job: dict) -> bool:
    """Work to do? Not closed, not waiting on the owner, not halted for the owner to look at."""
    return JOB_CLOSED not in tags(job) and not has_pending_approval(job) and not is_halted(job)


def is_context_request(approval: dict) -> bool:
    kind = str(approval.get("type", "")).lower()
    return "context" in kind


def approval_answer(approval: dict) -> str:
    for key in ("answer", "reply", "response", "context"):
        if approval.get(key):
            return str(approval[key])
    return ""


class DependencyCycle(ValueError):
    pass


def task_num(task: dict, index: int) -> int:
    try:
        return int(task.get("num", index + 1))
    except (TypeError, ValueError):
        return index + 1


def topo_order(tasks: list[dict]) -> list[int]:
    """Indexes of tasks in dependency order (stable: original order among ready tasks).
    Raises DependencyCycle for a cycle or a dependency on a task that doesn't exist."""
    nums = [task_num(t, i) for i, t in enumerate(tasks)]
    by_num = {n: i for i, n in enumerate(nums)}
    deps = {}
    for i, t in enumerate(tasks):
        d = set()
        for dep in _list(t.get("depends_on")):
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


def route(task: dict, index: int, agents: list) -> tuple[str, str, str]:
    """(name, role, how). Explicit task agent > agents[index] > senior staff (STEWARD)."""
    agents = [a for a in _list(agents) if isinstance(a, dict) and a.get("name")]
    explicit = task.get("agent")
    if isinstance(explicit, dict) and explicit.get("name"):
        return str(explicit["name"]), str(explicit.get("role") or task.get("role") or "agent"), "task"
    if isinstance(explicit, str) and explicit.strip():
        name = explicit.strip()
        role = task.get("role") or next((a.get("role") for a in agents if a["name"] == name), None)
        return name, str(role or "agent"), "task"
    if index < len(_list(agents)):
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


def draft_covers(draft: dict, num: int, agent: str, first_task_for_agent: bool, for_approval) -> bool:
    if not isinstance(draft, dict):
        return False
    if for_approval is not None:
        return draft.get("task") == num and draft.get("for_approval") == for_approval
    if "task" in draft:
        return draft.get("task") == num
    # Older hand-made drafts carry no task number: count one as the agent's first task.
    return first_task_for_agent and draft.get("agent") == agent


# ---------- the brain (reuses matrix/llm.py, same as the graph's specialists) ----------
def default_brain(name: str, role: str, in_roster: bool, text: str) -> str:
    from . import llm
    if in_roster:
        who = f"{name} ({roster()[name]}; role on this job: {role})"
    else:
        who = f"{name}, the team's {role}"
    mock = f"[{name} mock] Draft ({role}): {text.splitlines()[-1] if text else ''}"
    return llm.write(who, text, mock)


# ---------- storage ----------
class PgStore:
    """Postgres access. Detects `jobs` or "Jobs" at startup. Autocommit: every append is saved at once."""

    COLUMNS = ("message", "tasks", "agents", "drafts", "approvals", "step_log")

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self.table_name = self.detect_table()
        log.info("using table public.%s", self.table_name)

    def detect_table(self) -> str:
        rows = self.conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relname = ANY(%s)",
            [list(TABLE_CANDIDATES)]).fetchall()
        found = {r["relname"] for r in rows}
        for name in TABLE_CANDIDATES:
            if name in found:
                return name
        raise RuntimeError("no jobs table found (looked for public.jobs and public.\"Jobs\")")

    @property
    def table(self):
        from psycopg import sql
        return sql.Identifier("public", self.table_name)

    def candidates(self) -> list[dict]:
        from psycopg import sql
        q = sql.SQL("SELECT id, approvals, step_log FROM {} ORDER BY id").format(self.table)
        return self.conn.execute(q).fetchall()

    def claim(self, job_id: int) -> dict | None:
        """Lock the row (skip if someone else has it) and take a session advisory lock so the
        claim holds while each step commits on its own."""
        from psycopg import sql
        cols = sql.SQL(", ").join(sql.Identifier(c) for c in ("id",) + self.COLUMNS)
        q = sql.SQL("SELECT {} FROM {} WHERE id = %s FOR UPDATE SKIP LOCKED").format(cols, self.table)
        with self.conn.transaction():
            row = self.conn.execute(q, [job_id]).fetchone()
            if row is None:
                return None
            got = self.conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok",
                                    [LOCK_NAMESPACE, job_id]).fetchone()["ok"]
            if not got:
                return None
            if not is_pending(row):  # re-check under the lock
                self.release(job_id)
                return None
            self.append(row, "step_log", entry(PICKED_UP))
        return row

    def append(self, job: dict, column: str, item: dict) -> None:
        """Append one item to a jsonb array column, in the database and in `job`."""
        from psycopg import sql
        from psycopg.types.json import Jsonb
        assert column in self.COLUMNS
        q = sql.SQL("UPDATE {} SET {col} = COALESCE({col}, '[]'::jsonb) || %s WHERE id = %s").format(
            self.table, col=sql.Identifier(column))
        self.conn.execute(q, [Jsonb([item]), job["id"]])
        job[column] = _list(job.get(column)) + [item]

    def release(self, job_id: int) -> None:
        self.conn.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, job_id])

    def close(self) -> None:
        self.conn.close()


# ---------- processing ----------
@dataclass
class Sweep:
    max_llm_calls: int = MAX_LLM_CALLS_PER_SWEEP
    max_tasks_per_job: int = MAX_TASKS_PER_JOB_RUN
    llm_calls: int = 0
    processed: list = field(default_factory=list)


def process_job(job: dict, store, brain, sweep: Sweep) -> str:
    """Move one claimed job forward as far as allowed. Returns a short outcome word."""
    add = lambda tag, **d: store.append(job, "step_log", entry(tag, **d))  # noqa: E731
    try:
        tasks = [t for t in _list(job.get("tasks")) if isinstance(t, dict)]
        have = tags(job)
        if MESSAGE_RECEIVED not in have:
            add(MESSAGE_RECEIVED)
        if TASK_CREATED not in have:
            add(TASK_CREATED, count=len(tasks), tasks=[task_num(t, i) for i, t in enumerate(tasks)])

        # Approvals the owner has answered since we last looked.
        approvals = _list(job.get("approvals"))
        logged = {e.get("approval") for e in _list(job.get("step_log"))
                  if isinstance(e, dict) and e.get("tag") == APPROVAL_ANSWERED}
        for i, a in enumerate(approvals):
            if isinstance(a, dict) and i not in logged and status_of(a) != "pending":
                add(APPROVAL_ANSWERED, approval=i, status=status_of(a),
                    type="context request" if is_context_request(a) else "quick yes/no")
                if status_of(a) in NO:
                    add(REJECTED, approval=i)
                    add(JOB_CLOSED)
                    return "rejected"

        # An answered context request at the end of the thread means: re-draft with the answer.
        redraft_for, extra = None, ""
        if approvals and isinstance(approvals[-1], dict) and is_context_request(approvals[-1]) \
                and status_of(approvals[-1]) != "pending":
            redraft_for = len(approvals) - 1
            extra = (f"\n\nThe owner asked/was asked: {approvals[-1].get('text', '')}"
                     f"\nOwner's answer: {approval_answer(approvals[-1])}\nRevise your draft using this.")

        if not tasks:
            add("error: job has no tasks")
            return "halted"
        try:
            order = topo_order(tasks)
        except DependencyCycle as exc:
            add(CYCLE, detail=str(exc))
            return "halted"

        known = roster()
        drafts = _list(job.get("drafts"))
        seen_agents: set[str] = set()
        done_this_run = 0
        for idx in order:
            task = tasks[idx]
            num = task_num(task, idx)
            name, role, how = route(task, idx, job.get("agents"))
            first_for_agent = name not in seen_agents
            seen_agents.add(name)
            if any(draft_covers(d, num, name, first_for_agent, redraft_for) for d in drafts):
                continue  # already drafted (resumable)
            if done_this_run >= sweep.max_tasks_per_job:
                add(BREAKER, detail=f"task cap reached ({sweep.max_tasks_per_job} tasks per job run)")
                return "halted"
            if sweep.llm_calls >= sweep.max_llm_calls:
                add(BREAKER, detail=f"LLM call cap reached ({sweep.max_llm_calls} per sweep)")
                return "halted"
            add(AGENT_ASSIGNED, task=num, agent=name, role=role, via=how)
            in_roster = name in known
            if not in_roster:
                add(NOT_IN_ROSTER, task=num, agent=name)
            text = f"Job: {job.get('message', '')}\nYour task #{num}: {task.get('title', '')}{extra}"
            sweep.llm_calls += 1
            output = brain(name, role, in_roster, text)
            draft = {"task": num, "agent": name, "role": role, "output": output, "at": utc_now()}
            if redraft_for is not None:
                draft["for_approval"] = redraft_for
            store.append(job, "drafts", draft)
            drafts = _list(job.get("drafts"))
            add(DRAFT_SAVED, task=num, agent=name)
            done_this_run += 1

        # Everything is drafted. If the owner already said yes and nothing new was drafted, close.
        last = approvals[-1] if approvals and isinstance(approvals[-1], dict) else None
        if done_this_run == 0 and last is not None and status_of(last) in YES:
            add(JOB_CLOSED)
            return "closed"
        store.append(job, "approvals", {"from": "system", "to": "user", "type": "yes_no",
                                        "text": "Approve these drafts?", "status": "pending",
                                        "at": utc_now()})
        add(APPROVAL_ASKED, approval=len(_list(job.get("approvals"))) - 1)
        return "waiting"
    except Exception as exc:  # noqa: BLE001 - any error halts the job for the owner
        log.exception("job %s failed", job.get("id"))
        try:
            add(f"error: {type(exc).__name__}: {str(exc)[:200]}")
        except Exception:  # noqa: BLE001
            log.exception("could not record the error on job %s", job.get("id"))
        return "error"


def sweep_once(store, brain=default_brain, sweep: Sweep | None = None) -> Sweep:
    sweep = sweep or Sweep()
    for row in store.candidates():
        if not is_pending(row):
            continue
        if sweep.llm_calls >= sweep.max_llm_calls:
            log.info("LLM call cap for this sweep reached; job %s waits for the next sweep", row["id"])
            break
        job = store.claim(row["id"])
        if job is None:
            continue
        try:
            outcome = process_job(job, store, brain, sweep)
        finally:
            store.release(job["id"])
        log.info("job %s -> %s", job["id"], outcome)
        sweep.processed.append((job["id"], outcome))
    return sweep


# ---------- entry point ----------
def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "keeper_worker.log", maxBytes=1_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr is not None:  # pythonw has no console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.setLevel(logging.INFO)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Keeper worker")
    p.add_argument("--once", action="store_true", help="run one sweep and exit")
    p.add_argument("--interval", type=float, default=POLL_SECONDS, help="seconds between polls")
    args = p.parse_args(argv)
    setup_logging()
    if not config.CHECKPOINT_DB_URL:
        log.error("CHECKPOINT_DB_URL is not set in .env")
        return 2
    if not args.once:
        (LOG_DIR / "keeper_worker.pid").write_text(str(os.getpid()))
    log.info("keeper worker starting (pid %s, every %ss, LLM=%s, max %s LLM calls/sweep, %s tasks/job)",
             os.getpid(), args.interval, config.LLM_PROVIDER, MAX_LLM_CALLS_PER_SWEEP, MAX_TASKS_PER_JOB_RUN)
    store = None
    while True:
        try:
            if store is None:
                store = PgStore(config.CHECKPOINT_DB_URL)
            result = sweep_once(store)
            if result.processed:
                log.info("sweep done: %s", result.processed)
        except Exception:  # noqa: BLE001 - keep running; reconnect / re-detect the table next time
            log.exception("sweep failed; will reconnect")
            if store is not None:
                try:
                    store.close()
                except Exception:  # noqa: BLE001
                    pass
            store = None
            if args.once:
                return 1
        if args.once:
            if store is not None:
                store.close()
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
