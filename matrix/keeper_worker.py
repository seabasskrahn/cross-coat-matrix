"""Keeper worker: picks up jobs saved in Postgres (docs/KEEPER_SPEC.md) and has agents draft them.

The envelope rules live in matrix/envelope.py and the database code in matrix/keeper_store.py
(shared with the main Matrix flow). This file is just the loop and the job-processing steps.

What it does, every POLL seconds (default 10, env KEEPER_POLL_SECONDS):
  1. Finds pending jobs: not closed, no approval with status "pending" (those WAIT), not halted
     (last step_log tag is `circuit breaker`, `dependency cycle` or `error: ...`), and no owner
     answer younger than the grace window (KEEPER_ANSWER_GRACE_SECONDS, default 30), so a
     mis-click can be undone from the dashboard before anything happens.
  2. Claims one safely (SELECT ... FOR UPDATE SKIP LOCKED + advisory lock), logs `picked up`.
     Jobs the main Matrix flow is working on are locked, so they're skipped.
  3. Runs its tasks in dependency order. Each agent writes a DRAFT ONLY (never sends, pays,
     deletes or acts in the real world), saved as-is to `drafts`, using its own brain
     (llm.brain_for). A task that clearly needs outside facts may get ONE read-only Scout web
     search first (matrix/scout.py; capped per job, off without TAVILY_API_KEY), logged as
     `scout search` with the query only.
  .env is re-read at the start of every sweep, so changing the MAIN BRAIN line (LLM_PROVIDER) or a
  BRAIN_<AGENT> line takes effect within one poll, no restart (logged as `main brain changed`).
  4. Asks the owner "Approve these drafts?" in `approvals` and stops. It never approves itself.
     When the owner answers yes/no, the first poll after the grace window logs `approval answered`,
     then drafts any remaining tasks (and asks again) or logs `job closed` (a "no" logs `rejected`
     and `job closed`). An `approval reopened` step (dashboard Undo / Reopen) cancels that answer:
     the job waits on the approval again, even if it had been closed by the rejection.
  Switches (dashboard): a job with a `paused` step (no `resumed` after it) is skipped, and when the
  global switch in logs/keeper_settings.json is Off the worker stays alive but claims nothing.

Every change is written to the database immediately, so a crash loses nothing.
A halted job is left alone for the owner; to let the worker try again, append any step_log entry
after the halt tag (for example {"tag": "resume", "at": "..."}).

Run:   python -m matrix.keeper_worker           (loop forever)    or  run_keeper.bat
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
from pathlib import Path

from . import config, envelope as env, keeper_settings, llm, scout
from .keeper_store import PgStore

# ---------- settings ----------
POLL_SECONDS = float(os.getenv("KEEPER_POLL_SECONDS", "10"))
MAX_TASKS_PER_JOB_RUN = int(os.getenv("KEEPER_MAX_TASKS_PER_JOB", str(config.MAX_TASKS_PER_SWEEP)))  # 5
MAX_LLM_CALLS_PER_SWEEP = int(os.getenv("KEEPER_MAX_LLM_CALLS_PER_SWEEP", "5"))

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"

log = logging.getLogger("keeper_worker")


def draft_covers(draft: dict, num: int, agent: str, first_task_for_agent: bool, for_approval) -> bool:
    if not isinstance(draft, dict):
        return False
    if for_approval is not None:
        return num in env.draft_task_nums(draft) and draft.get("for_approval") == for_approval
    if "task" in draft:
        return num in env.draft_task_nums(draft)  # a batched draft covers several task numbers
    # Older hand-made drafts carry no task number: count one as the agent's first task.
    return first_task_for_agent and draft.get("agent") == agent


def default_brain(name: str, role: str, text: str) -> str:
    return env.brain(name, role, text)


@dataclass
class Sweep:
    max_llm_calls: int = MAX_LLM_CALLS_PER_SWEEP
    max_tasks_per_job: int = MAX_TASKS_PER_JOB_RUN
    llm_calls: int = 0
    processed: list = field(default_factory=list)
    off: bool = False  # the global On/Off switch was Off: nothing claimed


def process_job(job: dict, store, brain, sweep: Sweep) -> str:
    """Move one claimed job forward as far as allowed. Returns a short outcome word."""
    add = lambda tag, **d: store.append(job, "step_log", env.entry(tag, **d))  # noqa: E731
    try:
        tasks = [t for t in env.as_list(job.get("tasks")) if isinstance(t, dict)]
        have = env.tags(job)
        if env.MESSAGE_RECEIVED not in have:
            add(env.MESSAGE_RECEIVED)
        if env.TASK_CREATED not in have:
            add(env.TASK_CREATED, count=len(tasks), tasks=[env.task_num(t, i) for i, t in enumerate(tasks)])

        # Approvals the owner has answered since we last looked.
        approvals = env.as_list(job.get("approvals"))
        logged = env.acted_approvals(job)  # an `approval reopened` step un-logs its approval
        for i, a in enumerate(approvals):
            if isinstance(a, dict) and i not in logged and env.status_of(a) != env.PENDING:
                add(env.APPROVAL_ANSWERED, approval=i, status=env.status_of(a), type=env.exchange_tag(a))
                if env.status_of(a) in env.NO:
                    add(env.REJECTED, approval=i)
                    add(env.JOB_CLOSED)
                    return "rejected"

        # An answered context request at the end of the thread means: re-draft with the answer.
        redraft_for, extra = None, ""
        last = approvals[-1] if approvals and isinstance(approvals[-1], dict) else None
        if last is not None and env.is_context_request(last) and env.status_of(last) != env.PENDING:
            redraft_for = len(approvals) - 1
            extra = (f"\n\nThe owner asked/was asked: {last.get('text', '')}"
                     f"\nOwner's answer: {env.approval_answer(last)}\nRevise your draft using this.")

        if not tasks:
            add("error: job has no tasks")
            return "halted"
        try:
            order = env.topo_order(tasks)
        except env.DependencyCycle as exc:
            add(env.CYCLE, detail=str(exc))
            return "halted"

        known = env.roster()
        seen_agents: set[str] = set()
        done_this_run = 0
        for idx in order:
            task = tasks[idx]
            num = env.task_num(task, idx)
            name, role, how = env.route(task, idx, job.get("agents"))
            first_for_agent = name not in seen_agents
            seen_agents.add(name)
            if any(draft_covers(d, num, name, first_for_agent, redraft_for) for d in env.as_list(job.get("drafts"))):
                continue  # already drafted (resumable)
            if done_this_run >= sweep.max_tasks_per_job:
                add(env.BREAKER, detail=f"task cap reached ({sweep.max_tasks_per_job} tasks per job run)")
                return "halted"
            if sweep.llm_calls >= sweep.max_llm_calls:
                add(env.BREAKER, detail=f"LLM call cap reached ({sweep.max_llm_calls} per sweep)")
                return "halted"
            add(env.AGENT_ASSIGNED, task=num, agent=name, role=role, via=how)
            if name not in known:
                add(env.NOT_IN_ROSTER, task=num, agent=name)
            sweep.llm_calls += 1
            # Scout: optional read-only web search for outside facts (1 per task, capped per job).
            found = scout.research(str(task.get("title") or job.get("message", "")), [num], job.get("step_log"))
            if found is not None:
                add(env.SCOUT_SEARCH, **scout.step_fields(found, [num], name))
            web = found["extra"] if found is not None else ""
            output = brain(name, role, env.task_prompt(job.get("message", ""), task, extra + web))
            extra_fields = {"for_approval": redraft_for} if redraft_for is not None else {}
            if found is not None:
                extra_fields["scout"] = {"query": found["query"]}
            store.append(job, "drafts", env.draft(num, name, role, output, **extra_fields))
            add(env.DRAFT_SAVED, task=num, agent=name)
            done_this_run += 1

        # Everything is drafted. If the owner already said yes and nothing new was drafted, close.
        if done_this_run == 0 and last is not None and env.status_of(last) in env.YES:
            add(env.JOB_CLOSED)
            return "closed"
        store.append(job, "approvals", env.approval("Approve these drafts?"))
        add(env.APPROVAL_ASKED, approval=len(env.as_list(job.get("approvals"))) - 1)
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
    if not keeper_settings.worker_on():
        sweep.off = True  # switched Off from the dashboard: stay alive, claim nothing
        return sweep
    for row in store.candidates():
        if not env.is_pending(row):
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
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "keeper_worker.log", maxBytes=1_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    handlers = [fh]
    if sys.stderr is not None:  # pythonw has no console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        handlers.append(sh)
    for name in ("keeper_worker", "keeper_store", "matrix"):
        lg = logging.getLogger(name)
        for h in handlers:
            lg.addHandler(h)
        lg.setLevel(logging.INFO)


def log_brains() -> None:
    log.info("brains: main=%s; %s; scout: %s", config.LLM_PROVIDER,
             ", ".join(f"{a}={llm.brain_label(a)}" for a in env.roster()),
             f"on (max {config.SCOUT_MAX_SEARCHES_PER_JOB} searches/job)" if scout.enabled() else "off")


def reload_settings() -> bool:
    """Re-read .env at the start of every sweep, so the MAIN BRAIN line (and BRAIN_<AGENT> lines)
    take effect on the next sweep with no restart. Never crashes the loop."""
    try:
        changed = config.reload_env()
    except Exception:  # noqa: BLE001 - a bad .env keeps the current settings
        log.exception("could not re-read .env; keeping the current settings")
        return False
    if changed:
        log_brains()
    return changed


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
    log_brains()
    store = None
    was_off = None
    while True:
        reload_settings()  # picks up a MAIN BRAIN change in .env within one poll (~10 s)
        try:
            if store is None:
                store = PgStore(config.CHECKPOINT_DB_URL)
            result = sweep_once(store)
            if result.off != was_off:
                if result.off or was_off is not None:
                    log.info("worker switch is %s", "OFF: claiming no jobs" if result.off else "ON")
                was_off = result.off
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
