"""Keeper dashboard: the logic behind keeper_dashboard.py (a local page to watch and answer jobs).

Kept separate from the web server so it can be tested offline:
- job_status(job)            -> "Working" | "Waiting on you" | "Closed" | "Halted"
- apply_answer(...)          -> the change one Approve / Reject / answer click makes to an approval
- answer_approval(store,...) -> saves that change (Postgres: one transaction with SELECT ... FOR UPDATE)
- reopen_approval(store,...) -> Undo (answer not acted on yet) / Reopen (job closed by a rejection):
                                the approval goes back to pending + an `approval reopened` step.
                                Change to Yes / Change to No (an answer not acted on yet, or the one
                                that closed the job): an `approval reopened` step + the answer flipped
                                (fresh answered_at), so the worker acts on the new answer after the grace
- set_paused(store,...)      -> per-job On/Off: appends a `paused` / `resumed` step
- self_build_switch / render_persistent_tasks -> the separate "Self-build loop: ON/OFF" header toggle
                                and the "Always-running tasks" card ("Shut down task" instead of
                                Change to No). They only rewrite logs/keeper_settings.json, never a job.
- brain_panel(info)          -> header: main brain (live from .env), Gemini / xAI switch, each agent's
                                brain (pins marked), the worker's last reported brains (matrix/brain_switch.py)
- render_list / render_job / render_error -> the HTML pages

The dashboard only ever changes ONE approval entry in `jobs.approvals` (answer it, or put it back
to pending) and, for Undo / Reopen, appends one `approval reopened` step. The Keeper worker waits
out the grace window (KEEPER_ANSWER_GRACE_SECONDS, default 30), then logs `approval answered` and
closes or continues the job. The dashboard never closes jobs and never sends anything.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config
from . import envelope as env

WORKING, WAITING, CLOSED, HALTED, PAUSED = "Working", "Waiting on you", "Closed", "Halted", "Paused"
STATUSES = (WAITING, WORKING, PAUSED, HALTED, CLOSED)  # order of the summary strip
STATUS_CLASS = {WORKING: "working", WAITING: "waiting", CLOSED: "closed", HALTED: "halted", PAUSED: "paused"}
ANSWERED_BY = "owner (dashboard)"   # default; the web server passes "owner (dashboard session <id>)"


def session_label(short_id: str) -> str:
    """answered_by / changed_by text for one dashboard browser session (short id, not the secret)."""
    return f"owner (dashboard session {short_id})"
MAX_ANSWER = 4000
REFRESH_SECONDS = 10


class AnswerError(ValueError):
    """The click can't be saved (bad input, or that approval is no longer waiting)."""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


# ---------- status ----------
def job_status(job: dict) -> str:
    """Status badge from the envelope. First match wins:
    1. Closed          - step_log has a `job closed` tag with no `approval reopened` after it
    2. Paused          - a `paused` step with no `resumed` after it (per-job switch)
    3. Halted          - the last step_log tag (ignoring paused/resumed) is a halt tag
                         (dependency cycle, circuit breaker, error...)
    4. Waiting on you  - any approval has status "pending"
    5. Working         - anything else (the worker will pick it up or is on it)
    """
    if env.is_closed(job):
        return CLOSED
    if env.is_paused(job):
        return PAUSED
    if env.is_halted(job):
        return HALTED
    if env.has_pending_approval(job):
        return WAITING
    return WORKING


def summary(jobs: list[dict]) -> dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    for j in jobs:
        counts[job_status(j)] += 1
    return counts


# ---------- time ----------
def local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.TIMEZONE or "America/Edmonton")
    except Exception:  # noqa: BLE001
        return ZoneInfo("America/Edmonton")


def to_local(value) -> datetime | None:
    """ISO text ('...Z' = UTC) or a datetime -> local datetime. A naive datetime from Postgres
    (`timestamp` without zone, filled by now()) is already local time."""
    tz = local_tz()
    if isinstance(value, datetime):
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def fmt_time(value, now: datetime | None = None) -> str:
    """'7:09 PM' for today, 'Sat Sep 26, 7:09 PM' for other days, '' if unknown."""
    dt = to_local(value)
    if dt is None:
        return ""
    now = (now or datetime.now(timezone.utc)).astimezone(local_tz())
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now.date():
        return clock
    return f"{dt.strftime('%a %b')} {dt.day}, {clock}"


# ---------- approvals ----------
def apply_answer(approvals, index: int, action: str, text: str = "", now: str | None = None,
                 by: str = ANSWERED_BY, audit: dict | None = None) -> dict:
    """Validate one click and return the fields to merge into approvals[index].
    action: "yes" / "no" for a yes_no approval, "answer" (with text) for a context_request.
    by: who answered (the dashboard session). audit: {"at", "ip", "user_agent", "session"} kept
    with the answer (inside the approval's JSON, so no schema change)."""
    approvals = env.as_list(approvals)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(approvals):
        raise AnswerError("That approval doesn't exist.", 404)
    item = approvals[index]
    if not isinstance(item, dict):
        raise AnswerError("That approval entry is damaged.", 400)
    if env.status_of(item) != env.PENDING:
        raise AnswerError(f"That one was already answered ({env.status_of(item)}).", 409)
    changes = {"answered_at": now or env.utc_now(), "answered_by": by}
    if audit:
        changes["audit"] = dict(audit)
    text = (text or "").strip()
    if env.is_context_request(item):
        if action != "answer":
            raise AnswerError("This question needs a written answer.")
        if not text:
            raise AnswerError("Type an answer first.")
        if len(text) > MAX_ANSWER:
            raise AnswerError(f"Answer is too long (max {MAX_ANSWER} characters).")
        changes.update(status=env.ANSWERED, answer=text)
    else:
        if action not in ("yes", "no"):
            raise AnswerError("Choose Approve or Reject.")
        changes["status"] = action
    return changes


def answer_approval(store, job_id: int, index: int, action: str, text: str = "",
                    by: str = ANSWERED_BY, audit: dict | None = None) -> dict:
    """Save one click. Returns the updated approval entry.
    PgStore: one transaction, the row locked with SELECT ... FOR UPDATE, only approvals[index]
    changed (jsonb_set). Any other store (MemoryStore, fakes): get + update_item."""
    if hasattr(store, "conn") and hasattr(store, "table"):
        return _answer_pg(store, job_id, index, action, text, by, audit)
    job = store.get(job_id)
    if job is None:
        raise AnswerError("That job doesn't exist.", 404)
    changes = apply_answer(job.get("approvals"), index, action, text, by=by, audit=audit)
    store.update_item(job_id, "approvals", index, changes)
    return {**job["approvals"][index], **changes}


def _answer_pg(store, job_id: int, index: int, action: str, text: str, by: str = ANSWERED_BY,
               audit: dict | None = None) -> dict:
    from psycopg import sql
    from psycopg.types.json import Jsonb
    with store._mutex, store.conn.transaction():
        row = store.conn.execute(
            sql.SQL("SELECT approvals FROM {} WHERE id = %s FOR UPDATE").format(store.table), [job_id]).fetchone()
        if row is None:
            raise AnswerError("That job doesn't exist.", 404)
        changes = apply_answer(row["approvals"], index, action, text, by=by, audit=audit)
        new = store.conn.execute(
            sql.SQL("UPDATE {} SET approvals = jsonb_set(approvals, ARRAY[%s::text], (approvals -> %s) || %s) "
                    "WHERE id = %s RETURNING approvals -> %s AS item").format(store.table),
            [str(index), index, Jsonb(changes), job_id, index]).fetchone()
    return new["item"]


UNDO, REOPEN, CHANGE_TO_YES, CHANGE_TO_NO = "undo", "reopen", "change to yes", "change to no"
CHANGE_TARGET = {CHANGE_TO_YES: "yes", CHANGE_TO_NO: "no"}


def can_change(job: dict, to: str) -> list[int]:
    """yes/no approvals the owner can flip straight to `to` ("yes" or "no"): answered the other way
    and not acted on yet (inside or after the grace window), or the answer that closed the job."""
    approvals = env.as_list(job.get("approvals"))
    other = env.NO if to == "yes" else env.YES
    found = set(env.unacted_answers(job))
    closing = env.closing_answer(job)
    if closing is not None:
        found.add(closing[0])
    return sorted(i for i in found if isinstance(approvals[i], dict) and not env.is_context_request(approvals[i])
                  and env.status_of(approvals[i]) in other)


def can_change_to_yes(job: dict) -> list[int]:
    return can_change(job, "yes")


def plan_reopen(job: dict, index, mode: str, now: str | None = None, by: str = ANSWERED_BY,
                audit: dict | None = None) -> tuple[int, dict, dict]:
    """Check an Undo / Reopen click. Returns (index, new approval entry, step_log entry).
    Undo:   approval `index` was answered and the worker hasn't acted on it yet.
    Reopen: the job was closed because approval `index` was rejected (index may be None)."""
    approvals = env.as_list(job.get("approvals"))
    if mode == UNDO:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(approvals) \
                or not isinstance(approvals[index], dict):
            raise AnswerError("That approval doesn't exist.", 404)
        if env.status_of(approvals[index]) == env.PENDING:
            raise AnswerError("That approval is already waiting for an answer.", 409)
        if index not in env.unacted_answers(job):
            if env.reopenable_approval(job) == index:
                raise AnswerError("Too late to undo: the Keeper worker already closed this job. "
                                  "Use Reopen instead.", 409)
            raise AnswerError("Too late to undo: the Keeper worker already acted on this answer.", 409)
        note = "answer undone by the owner before the worker acted"
    elif mode in CHANGE_TARGET:
        to = CHANGE_TARGET[mode]
        closing = env.closing_answer(job)
        if index is None and closing is not None:
            index = closing[0]
        if index not in can_change(job, to):
            was = "an Approve" if to == "no" else "a Reject"
            raise AnswerError(f"Only {was} can be changed to {to.title()}, and only while the worker hasn't "
                              "gone on past it.", 409)
        note = f"owner changed the answer from {'no' if to == 'yes' else 'yes'} to {to}"
    elif mode == REOPEN:
        found = env.reopenable_approval(job)
        if found is None:
            raise AnswerError("Only a job closed by a Reject can be reopened.", 409)
        if index is not None and index != found:
            raise AnswerError("That isn't the approval that closed this job.", 409)
        index = found
        note = "rejected job reopened by the owner"
    else:
        raise AnswerError("Unknown action.")
    stamp = now or env.utc_now()
    step = env.entry(env.APPROVAL_REOPENED, approval=index, via=mode, by=by, note=note,
                     was=env.status_of(approvals[index]))
    step["at"] = stamp
    if audit:
        step["audit"] = dict(audit)
    item = env.reopened(approvals[index], stamp)
    if mode in CHANGE_TARGET:
        item.update(status=CHANGE_TARGET[mode], answered_at=stamp, answered_by=by,
                    changed_from=env.status_of(approvals[index]))
        if audit:
            item["audit"] = dict(audit)
    return index, item, step


def reopen_approval(store, job_id: int, index, mode: str, by: str = ANSWERED_BY, audit: dict | None = None) -> dict:
    """Save an Undo / Reopen: the approval back to pending + an `approval reopened` step, together.
    PgStore: one transaction; row locked (SELECT ... FOR UPDATE) and refused if the worker holds the
    job's advisory lock (it's acting on the job right now). Returns the new approval entry."""
    if hasattr(store, "conn") and hasattr(store, "table"):
        return _reopen_pg(store, job_id, index, mode, by, audit)
    job = store.get(job_id)
    if job is None:
        raise AnswerError("That job doesn't exist.", 404)
    if job_id in getattr(store, "locked", ()):
        raise AnswerError(BUSY, 409)
    index, item, step = plan_reopen(job, index, mode, by=by, audit=audit)
    approvals = env.as_list(job.get("approvals"))
    approvals[index] = item
    store.set_field(job_id, "approvals", approvals)
    store.append(job_id, "step_log", step)
    return item


BUSY = "The Keeper worker is working on this job right now. Try again in a few seconds."


def _reopen_pg(store, job_id: int, index, mode: str, by: str = ANSWERED_BY, audit: dict | None = None) -> dict:
    from psycopg import sql
    from psycopg.types.json import Jsonb
    from .keeper_store import LOCK_NAMESPACE
    with store._mutex, store.conn.transaction():
        row = store.conn.execute(
            sql.SQL("SELECT id, approvals, step_log FROM {} WHERE id = %s FOR UPDATE").format(store.table),
            [job_id]).fetchone()
        if row is None:
            raise AnswerError("That job doesn't exist.", 404)
        free = store.conn.execute("SELECT pg_try_advisory_xact_lock(%s, %s) AS ok",
                                  [LOCK_NAMESPACE, job_id]).fetchone()["ok"]
        if not free:
            raise AnswerError(BUSY, 409)
        index, item, step = plan_reopen(row, index, mode, by=by, audit=audit)
        store.conn.execute(
            sql.SQL("UPDATE {} SET approvals = jsonb_set(approvals, ARRAY[%s::text], %s), "
                    "step_log = COALESCE(step_log, '[]'::jsonb) || %s WHERE id = %s").format(store.table),
            [str(index), Jsonb(item), Jsonb([step]), job_id])
    return item


def set_paused(store, job_id: int, paused: bool, by: str = ANSWERED_BY) -> bool:
    """Per-job On/Off: append a `paused` or `resumed` step (under a row lock on Postgres).
    Returns the new paused state. A closed job can't be paused; asking for the current state is a no-op."""
    def plan(job):
        if env.is_closed(job):
            raise AnswerError("This job is closed; there's nothing to pause.", 409)
        if env.is_paused(job) == paused:
            return None
        return env.entry(env.PAUSED if paused else env.RESUMED, by=by)

    if hasattr(store, "conn") and hasattr(store, "table"):
        from psycopg import sql
        from psycopg.types.json import Jsonb
        with store._mutex, store.conn.transaction():
            row = store.conn.execute(sql.SQL("SELECT id, step_log FROM {} WHERE id = %s FOR UPDATE")
                                     .format(store.table), [job_id]).fetchone()
            if row is None:
                raise AnswerError("That job doesn't exist.", 404)
            step = plan(row)
            if step is not None:
                store.conn.execute(sql.SQL("UPDATE {} SET step_log = COALESCE(step_log, '[]'::jsonb) || %s "
                                           "WHERE id = %s").format(store.table), [Jsonb([step]), job_id])
        return paused
    job = store.get(job_id)
    if job is None:
        raise AnswerError("That job doesn't exist.", 404)
    step = plan(job)
    if step is not None:
        store.append(job_id, "step_log", step)
    return paused


# ---------- reading jobs ----------
LIST_COLUMNS = ("id", "message", "tasks", "agents", "approvals", "step_log", "created_at")
DETAIL_COLUMNS = ("id", "message", "tasks", "agents", "drafts", "approvals", "step_log", "created_at")


def _pg_select(store, columns, where: str = "", params=()):
    from psycopg import sql
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    return store._exec(lambda t: sql.SQL("SELECT {} FROM {} " + where).format(cols, t), params)


def list_jobs(store) -> list[dict]:
    if hasattr(store, "conn") and hasattr(store, "table"):
        return _pg_select(store, LIST_COLUMNS, "ORDER BY id DESC").fetchall()
    return list(reversed(store.candidates()))


def get_job(store, job_id: int) -> dict | None:
    if hasattr(store, "conn") and hasattr(store, "table"):
        return _pg_select(store, DETAIL_COLUMNS, "WHERE id = %s", [job_id]).fetchone()
    return store.get(job_id)


# ---------- HTML ----------
def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def short(text, n: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


def badge(status: str) -> str:
    return f'<span class="badge {STATUS_CLASS.get(status, "working")}">{esc(status)}</span>'


def agent_label(a) -> str:
    if isinstance(a, dict):
        name, role = a.get("name"), a.get("role")
        return f"{name} ({role})" if role else str(name or "")
    return str(a or "")


def tag_class(tag: str) -> str:
    if env.is_halt_tag(tag) or tag == env.REJECTED:
        return "t-bad"
    if tag in (env.APPROVAL_ASKED,):
        return "t-wait"
    if tag == env.APPROVAL_REOPENED:
        return "t-undo"
    if tag in (env.APPROVAL_ANSWERED, env.JOB_CLOSED):
        return "t-done"
    if tag in env.STARTER_TAGS:
        return "t-std"
    return "t-free"


STYLE = r"""
  :root { --bg:#f4f6fb; --card:#fff; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--ink); font-size:14px; }
  header { background:#111827; color:#fff; padding:16px 28px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  header h1 { font-size:20px; margin:0; flex:1; } header a { color:#93c5fd; text-decoration:none; }
  header p { margin:0; color:#cbd5e1; font-size:13px; width:100%; }
  main { max-width:1200px; margin:0 auto; padding:18px 28px 60px; }
  h2 { font-size:16px; margin:24px 0 8px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  .pad { padding:12px 16px; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
       background:#f9fafb; padding:9px 12px; border-bottom:1px solid var(--line); }
  td { padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  tr.row:hover td { background:#f8fafc; cursor:pointer; }
  a.job { color:var(--ink); text-decoration:none; font-weight:600; }
  .id { font-family:Consolas,monospace; color:var(--muted); }
  .muted { color:var(--muted); } .small { font-size:12px; }
  .badge { display:inline-block; font-size:12px; font-weight:600; padding:2px 10px; border-radius:99px; white-space:nowrap; }
  .badge.working { background:#dbeafe; color:#1e40af; } .badge.waiting { background:#fef3c7; color:#92400e; }
  .badge.closed { background:#dcfce7; color:#166534; } .badge.halted { background:#fee2e2; color:#991b1b; }
  .badge.paused { background:#e5e7eb; color:#374151; }
  .badge.pending { background:#fef3c7; color:#92400e; } .badge.yes, .badge.answered { background:#dcfce7; color:#166534; }
  .badge.no { background:#fee2e2; color:#991b1b; } .badge.other { background:#f3f4f6; color:#374151; }
  .strip { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:6px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 16px; min-width:140px; border-left-width:6px; }
  .stat b { display:block; font-size:26px; line-height:1.1; } .stat span { color:var(--muted); font-size:13px; }
  .stat.waiting { border-left-color:#f59e0b; } .stat.working { border-left-color:#3b82f6; }
  .stat.halted { border-left-color:#ef4444; } .stat.paused { border-left-color:#9ca3af; } .stat.closed { border-left-color:#22c55e; } .stat.total { border-left-color:#6b7280; }
  .chip { display:inline-block; font-size:12px; padding:1px 8px; border-radius:99px; background:#eef2ff; color:#3730a3; margin:1px 2px 1px 0; }
  pre { margin:0; white-space:pre-wrap; word-wrap:break-word; font-family:Consolas,monospace; font-size:13px;
        background:#f9fafb; border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
  .msg { font-size:15px; white-space:pre-wrap; }
  .draft { border-bottom:1px solid var(--line); padding:12px 16px; } .draft:last-child { border-bottom:none; }
  .draft h3 { margin:0 0 6px; font-size:14px; }
  .appr { border-bottom:1px solid var(--line); padding:12px 16px; } .appr:last-child { border-bottom:none; }
  .appr.pending { background:#fffbeb; }
  .appr .top { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:4px; }
  .appr .text { font-size:15px; margin:4px 0; white-space:pre-wrap; }
  .actions { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; align-items:flex-start; }
  textarea { width:100%; min-height:70px; padding:8px 10px; border:1px solid #d1d5db; border-radius:8px; font:inherit; }
  button { font:inherit; font-size:14px; padding:8px 18px; border-radius:8px; border:1px solid transparent; cursor:pointer; }
  .approve { background:#16a34a; color:#fff; } .approve:hover { background:#15803d; }
  .reject { background:#fff; color:#b91c1c; border-color:#fecaca; } .reject:hover { background:#fef2f2; }
  .send { background:var(--blue); color:#fff; } .send:hover { background:#1d4ed8; }
  .timeline { list-style:none; margin:0; padding:6px 16px; }
  .timeline li { display:grid; grid-template-columns:150px 170px 1fr; gap:10px; padding:6px 0; border-bottom:1px dashed var(--line); }
  .timeline li:last-child { border-bottom:none; }
  .tag { display:inline-block; font-size:12px; padding:1px 9px; border-radius:6px; font-weight:600; }
  .t-std { background:#e0e7ff; color:#3730a3; } .t-free { background:#f3f4f6; color:#374151; }
  .t-wait { background:#fef3c7; color:#92400e; } .t-done { background:#dcfce7; color:#166534; } .t-bad { background:#fee2e2; color:#991b1b; }
  .t-undo { background:#f3e8ff; color:#6b21a8; }
  .undo { background:#fff; color:#6b21a8; border-color:#d8b4fe; } .undo:hover { background:#faf5ff; }
  .reopen { background:#fff; color:#6b21a8; border-color:#d8b4fe; } .reopen:hover { background:#faf5ff; }
  .later { color:var(--muted); font-size:13px; align-self:center; }
  .toyes { background:#16a34a; color:#fff; } .toyes:hover { background:#15803d; }
  .tono { background:#fff; color:#b91c1c; border-color:#fecaca; } .tono:hover { background:#fef2f2; }
  .shutdown { background:#fff; color:#9a3412; border-color:#fdba74; } .shutdown:hover { background:#fff7ed; }
  .startup { background:#16a34a; color:#fff; } .startup:hover { background:#15803d; }
  .switch { display:inline-flex; align-items:center; gap:8px; cursor:pointer; user-select:none; font-size:14px; }
  .switch input { display:none; }
  .switch .knob { width:42px; height:24px; border-radius:99px; background:#9ca3af; position:relative; transition:.15s; }
  .switch .knob::after { content:""; position:absolute; top:3px; left:3px; width:18px; height:18px; border-radius:50%;
                         background:#fff; transition:.15s; }
  .switch input:checked + .knob { background:#16a34a; } .switch input:checked + .knob::after { left:21px; }
  .off-banner { background:#fee2e2; border:1px solid #fecaca; color:#991b1b; border-radius:12px; padding:10px 16px;
                margin-bottom:12px; font-weight:600; }
  .pause-banner { background:#f3f4f6; border:1px solid #d1d5db; color:#374151; border-radius:12px; padding:10px 16px;
                  margin-bottom:12px; }
  #note { margin:0 0 14px; padding:10px 14px; border-radius:8px; display:none; white-space:pre-line; }
  #note.ok { display:block; background:#dcfce7; color:#166534; } #note.err { display:block; background:#fee2e2; color:#991b1b; }
  .problem { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:12px; padding:18px 20px; font-size:15px; }
  .problem h2 { margin:0 0 8px; }
  .brain { display:flex; flex-direction:column; gap:3px; font-size:13px; color:#e5e7eb; }
  .brain .row1 { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .seg { display:inline-flex; border:1px solid #4b5563; border-radius:8px; overflow:hidden; }
  .seg button { border:none; border-radius:0; padding:5px 12px; background:#1f2937; color:#e5e7eb; font-size:13px; }
  .seg button.sel { background:#2563eb; color:#fff; font-weight:600; cursor:default; }
  .seg button:not(.sel):hover { background:#374151; }
  .brain .agents, .brain .reported { font-size:11px; color:#9ca3af; max-width:640px; }
  .brain .pin { color:#fbbf24; }
"""

SCRIPT = r"""
const KEEPER_TOKEN = (document.querySelector('meta[name="keeper-token"]') || {}).content || "";
function jsonHeaders() { return {"Content-Type":"application/json", "X-Keeper-Token": KEEPER_TOKEN}; }
function say(text, ok) { const m = document.getElementById("note"); if (!m) return; m.textContent = text; m.className = ok ? "ok" : "err"; window.scrollTo(0, 0); }
async function answer(job, index, action) {
  let text = "";
  if (action === "no" && !confirm("Reject this? The Keeper worker will stop this job.")) return;
  if (action === "answer") {
    text = document.getElementById("ans-" + index).value.trim();
    if (!text) { say("Type an answer first.", false); return; }
  }
  try {
    const r = await fetch("/api/answer", {method:"POST", headers:jsonHeaders(),
      body:JSON.stringify({job:job, index:index, action:action, text:text})});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "Something went wrong");
    sessionStorage.setItem("keeper-note", "Saved. You can Undo for GRACE seconds; after that the Keeper worker picks it up within about 10 seconds.");
    location.reload();
  } catch (e) { say("Not saved: " + e.message, false); }
}
async function post(url, body, note) {
  try {
    const r = await fetch(url, {method:"POST", headers:jsonHeaders(), body:JSON.stringify(body)});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "Something went wrong");
    sessionStorage.setItem("keeper-note", note);
    location.reload();
  } catch (e) { say("Not saved: " + e.message, false); }
}
function undo(job, index) {
  post("/api/undo", {job:job, index:index}, "Undone. That approval is waiting for your answer again.");
}
function changeToYes(job, index) {
  if (!confirm("Change your answer to YES? The Keeper worker will carry on with this job (you can still Undo for GRACE seconds).")) return;
  post("/api/change-to-yes", {job:job, index:index}, "Changed to Yes. You can Undo for GRACE seconds; then the Keeper worker carries on.");
}
function changeToNo(job, index) {
  if (!confirm("Change your answer to NO? The job will be marked rejected. Drafts already made stay as they are (nothing was ever sent). You can still Undo for GRACE seconds.")) return;
  post("/api/change-to-no", {job:job, index:index}, "Changed to No. You can Undo for GRACE seconds; then the Keeper worker marks the job rejected.");
}
function setWorker(on) {
  post("/api/worker", {on:on}, on ? "Keeper worker switched ON. It picks up jobs again within about 10 seconds."
                                  : "Keeper worker switched OFF. It stays running but won't pick up any job.");
}
function setSelfBuild(on) {
  if (!on && !confirm("Switch the self-build loop OFF?\n\nIt stops at its next round. No job or approval changes.")) { location.reload(); return; }
  post("/api/self-build", {on:on}, on ? "Self-build loop switched ON. It carries on at its next round."
                                      : "Self-build loop switched OFF. It stops at its next round. No approval was changed.");
}
function shutDownTask(task) {
  if (!confirm("Shut down this always-running task?\n\nIt stops at its next round. This is not a rejection: no job or approval changes, and you can switch it back on any time.")) return;
  post("/api/shutdown-task", {task:task}, "Task shut down. It stops at its next round. No approval was changed.");
}
function setBrain(value, label, current) {
  if (value === current) return;
  if (!confirm("Switch the main brain to " + label + "?\n\nEvery agent without its own pin follows it. BEZEL stays on Gemini and STEWARD on xAI (pinned). Only the LLM_PROVIDER line in .env changes (a backup is saved first).")) return;
  post("/api/brain", {provider:value}, "Switched to " + label + "; the Keeper picks it up within about 10 seconds.");
}
function setBezel(value, label, current) {
  if (value === current) return;
  if (!confirm("Switch BEZEL's brain to " + label + "?\n\nOnly the BRAIN_BEZEL line in .env changes (added if missing; a backup is saved first). The main brain and STEWARD stay as they are.")) return;
  post("/api/bezel-brain", {provider:value}, "BEZEL switched to " + label + "; the Keeper picks it up within about 10 seconds.");
}
function setPaused(job, paused) {
  post("/api/pause", {job:job, paused:paused}, paused ? "Job paused. The Keeper worker will skip it until you switch it back on."
                                                     : "Job switched back on.");
}
function reopenJob(job) {
  if (!confirm("Reopen this job? Your Reject is taken back and the job waits for your answer again.")) return;
  post("/api/reopen", {job:job}, "Reopened. The job is waiting for your answer again.");
}
function tick() {
  for (const b of document.querySelectorAll("[data-deadline]")) {
    const left = Math.ceil((Number(b.dataset.deadline) - Date.now()) / 1000);
    const hint = document.getElementById(b.dataset.hint);
    if (hint) hint.textContent = left > 0 ? left + "s left to undo" : "the worker will act any moment";
  }
}
tick(); setInterval(tick, 1000);
const saved = sessionStorage.getItem("keeper-note");
if (saved) { sessionStorage.removeItem("keeper-note"); say(saved, true); }
// Refresh every 10 seconds, unless you're typing an answer.
setInterval(() => {
  const typing = [...document.querySelectorAll("textarea")].some(t => t.value.trim() || t === document.activeElement);
  if (!typing) location.reload();
}, REFRESH_MS);
"""


def page(title: str, body: str, subtitle: str = "", back: bool = False, switch: str = "") -> str:
    nav = '<a href="/">&larr; All jobs</a>' if back else ""
    script = SCRIPT.replace("REFRESH_MS", str(REFRESH_SECONDS * 1000)).replace("GRACE", f"{env.grace_seconds():g}")
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)} - Keeper dashboard</title>\n<style>{STYLE}</style></head>\n<body>\n"
        f"<header><h1>{esc(title)}</h1>{switch}{nav}<p>{esc(subtitle)}</p></header>\n"
        f'<main><div id="note"></div>\n{body}\n</main>\n<script>{script}</script>\n</body></html>\n'
    )


def render_summary(counts: dict[str, int]) -> str:
    stats = "".join(f'<div class="stat {STATUS_CLASS[s]}"><b>{counts.get(s, 0)}</b><span>{esc(s)}</span></div>'
                    for s in STATUSES)
    total = sum(counts.values())
    return f'<div class="strip">{stats}<div class="stat total"><b>{total}</b><span>All jobs</span></div></div>'


def undo_chip(job: dict, now: datetime | None = None) -> str:
    if env.in_grace(job, now):
        return ' <span class="chip">undo open</span>'
    return ""


def render_list(jobs: list[dict], now: datetime | None = None, settings: dict | None = None,
                brain: dict | None = None) -> str:
    rows = []
    for j in jobs:
        jid = j.get("id")
        agents = ", ".join(esc(agent_label(a)) for a in env.as_list(j.get("agents"))) or '<span class="muted">none yet</span>'
        rows.append(
            f'<tr class="row" onclick="location.href=\'/job/{esc(jid)}\'">'
            f'<td class="id">#{esc(jid)}</td>'
            f'<td><a class="job" href="/job/{esc(jid)}">{esc(short(j.get("message")))}</a></td>'
            f"<td>{badge(job_status(j))}{undo_chip(j, now)}</td>"
            f'<td style="text-align:center">{len(env.as_list(j.get("tasks")))}</td>'
            f"<td>{agents}</td>"
            f'<td style="white-space:nowrap">{esc(fmt_time(j.get("created_at"), now))}</td></tr>')
    table = ("".join(rows) if rows else
             '<tr><td colspan="6" class="muted" style="padding:18px">No jobs yet.</td></tr>')
    body = (worker_banner(settings) + render_summary(summary(jobs)) + render_persistent_tasks(settings, now) +
            '<h2>Jobs</h2><div class="card"><table><thead><tr><th style="width:60px">Job</th><th>Message</th>'
            '<th style="width:130px">Status</th><th style="width:60px">Tasks</th><th>Agents</th>'
            f'<th style="width:160px">Created</th></tr></thead><tbody>{table}</tbody></table></div>')
    tz = local_tz().key
    return page("Keeper dashboard", body,
                f"Every job Keeper is holding. Click a job to see its drafts and answer approvals. "
                f"Updates every {REFRESH_SECONDS} seconds. Times are {tz}.",
                switch=worker_switch(settings) + self_build_switch(settings) + brain_panel(brain))


def _render_tasks(job: dict) -> str:
    tasks = env.as_list(job.get("tasks"))
    if not tasks:
        return '<div class="card pad muted">No tasks yet.</div>'
    drafted = set()
    for d in env.as_list(job.get("drafts")):
        if isinstance(d, dict):
            for n in (d.get("task") if isinstance(d.get("task"), list) else [d.get("task")]):
                drafted.add(str(n))
    rows = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        num = env.task_num(t, i)
        deps = ", ".join(f"#{esc(d)}" for d in env.as_list(t.get("depends_on"))) or '<span class="muted">none</span>'
        a = t.get("agent")
        who = esc(agent_label(a)) if a else '<span class="muted">not assigned</span>'
        done = '<span class="badge yes">drafted</span>' if str(num) in drafted else '<span class="badge other">no draft</span>'
        rows.append(f'<tr><td class="id">#{num}</td><td>{esc(t.get("title"))}</td><td>{deps}</td>'
                    f"<td>{who}</td><td>{done}</td></tr>")
    return ('<div class="card"><table><thead><tr><th style="width:50px">#</th><th>Task</th>'
            '<th style="width:110px">Depends on</th><th style="width:220px">Agent (role)</th>'
            f'<th style="width:100px">Draft</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _render_drafts(job: dict, now) -> str:
    drafts = [d for d in env.as_list(job.get("drafts")) if isinstance(d, dict)]
    if not drafts:
        return '<div class="card pad muted">No drafts yet.</div>'
    out = []
    for d in drafts:
        output = d.get("output")
        text = output if isinstance(output, str) else json.dumps(output, indent=2, ensure_ascii=False)
        who = agent_label({"name": d.get("agent"), "role": d.get("role")})
        out.append(f'<div class="draft"><h3>Task {esc(d.get("task"))} &middot; {esc(who)} '
                   f'<span class="muted small">{esc(fmt_time(d.get("at"), now))}</span></h3><pre>{esc(text)}</pre></div>')
    return f'<div class="card">{"".join(out)}</div>'


def _yes_button(jid, i: int) -> str:
    return f'<button class="toyes" onclick="changeToYes({esc(jid)}, {i})">Change to Yes</button>'


def _no_button(jid, i: int) -> str:
    return f'<button class="tono" onclick="changeToNo({esc(jid)}, {i})">Change to No</button>'


def _change_buttons(job: dict, i: int) -> str:
    jid = job.get("id")
    return ((_yes_button(jid, i) if i in can_change(job, "yes") else "")
            + (_no_button(jid, i) if i in can_change(job, "no") else ""))


def _undo_controls(jid, i: int, item: dict, now, to_yes: str) -> str:
    deadline = env.answer_deadline(item)
    now = now or datetime.now(timezone.utc)
    if deadline is not None and now < deadline:
        ms = int(deadline.timestamp() * 1000)
        hint = f'{max(1, int((deadline - now).total_seconds() + 0.999))}s left to undo'
        return (f'<div class="actions"><button class="undo" data-deadline="{ms}" data-hint="undo-hint-{i}" '
                f'onclick="undo({esc(jid)}, {i})">Undo</button>{to_yes}'
                f'<span class="later" id="undo-hint-{i}">{esc(hint)}</span></div>')
    return (f'<div class="actions"><button class="undo" onclick="undo({esc(jid)}, {i})">Undo</button>{to_yes}'
            f'<span class="later">the Keeper worker hasn\'t acted on this yet</span></div>')


def _render_approvals(job: dict, now) -> str:
    items = env.as_list(job.get("approvals"))
    if not items:
        return '<div class="card pad muted">No approvals asked yet.</div>'
    jid = job.get("id")
    unacted = set(env.unacted_answers(job))
    closing = env.closing_answer(job)
    out = []
    for i, a in enumerate(items):
        if not isinstance(a, dict):
            continue
        st = env.status_of(a)
        cls = st if st in ("pending", "yes", "no", "answered") else "other"
        kind = "Question for you" if env.is_context_request(a) else "Yes / no"
        head = (f'<span class="badge {cls}">{esc(st)}</span><b>{esc(a.get("from", "?"))} &rarr; {esc(a.get("to", "?"))}</b>'
                f'<span class="chip">{esc(kind)}</span><span class="muted small">{esc(fmt_time(a.get("at"), now))}</span>')
        extra = ""
        ans = env.approval_answer(a)
        if ans:
            extra += f'<div class="small"><b>Answer:</b></div><pre>{esc(ans)}</pre>'
        if a.get("answered_at") or a.get("answered_by"):
            extra += (f'<div class="muted small">Answered {esc(fmt_time(a.get("answered_at"), now))}'
                      f'{" by " + esc(a.get("answered_by")) if a.get("answered_by") else ""}</div>')
        if i in unacted:
            extra += _undo_controls(jid, i, a, now, _change_buttons(job, i))
        elif closing is not None and i == closing[0]:
            if closing[1] == "no":
                extra += (f'<div class="actions">{_change_buttons(job, i)}'
                          f'<button class="reopen" onclick="reopenJob({esc(jid)})">Reopen</button>'
                          f'<span class="later">this Reject closed the job</span></div>')
            else:
                extra += (f'<div class="actions">{_change_buttons(job, i)}'
                          f'<span class="later">{esc(CHANGE_TO_NO_NOTE)}</span></div>')
        if st == env.PENDING:
            if env.is_context_request(a):
                extra += (f'<div class="actions" style="display:block"><textarea id="ans-{i}" maxlength="{MAX_ANSWER}" '
                          f'placeholder="Type your answer here"></textarea>'
                          f'<button class="send" onclick="answer({esc(jid)}, {i}, \'answer\')">Send answer</button></div>')
            else:
                extra += (f'<div class="actions"><button class="approve" onclick="answer({esc(jid)}, {i}, \'yes\')">Approve</button>'
                          f'<button class="reject" onclick="answer({esc(jid)}, {i}, \'no\')">Reject</button></div>')
        out.append(f'<div class="appr {cls}"><div class="top">{head}</div>'
                   f'<div class="text">{esc(a.get("text"))}</div>{extra}</div>')
    return f'<div class="card">{"".join(out)}</div>'


def _detail_bits(e: dict) -> str:
    bits = []
    for k, v in e.items():
        if k in ("tag", "at") or v in (None, "", [], {}):
            continue
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        bits.append(f'<span class="muted">{esc(k)}:</span> {esc(short(v, 200))}')
    return " &middot; ".join(bits)


def _render_timeline(job: dict, now) -> str:
    steps = [e for e in env.as_list(job.get("step_log")) if isinstance(e, dict)]
    if not steps:
        return '<div class="card pad muted">Nothing logged yet.</div>'
    items = "".join(
        f'<li><span class="small">{esc(fmt_time(e.get("at"), now)) or "?"}</span>'
        f'<span><span class="tag {tag_class(str(e.get("tag", "")))}">{esc(e.get("tag", ""))}</span></span>'
        f'<span class="small">{_detail_bits(e)}</span></li>' for e in steps)
    return f'<div class="card"><ul class="timeline">{items}</ul></div>'


CHANGE_TO_NO_NOTE = ("Change to No only records the reversal and marks the job rejected. Drafts already made "
                     "stay as they are; nothing was ever sent, so there's nothing to call back.")


def toggle(on: bool, onchange: str, label: str) -> str:
    return (f'<label class="switch"><input type="checkbox" {"checked" if on else ""} onchange="{onchange}">'
            f'<span class="knob"></span><span>{esc(label)}</span></label>')


def worker_switch(settings: dict | None) -> str:
    """The global On/Off switch for the header ('' when settings aren't known)."""
    if settings is None:
        return ""
    on = bool(settings.get("worker_on"))
    return toggle(on, "setWorker(this.checked)", "Keeper worker: On" if on else "Keeper worker: Off")


SELF_BUILD_TASK = "self-build"
PERSISTENT_TASKS = {SELF_BUILD_TASK: "Self-build loop"}  # always-running tasks shown in their own card


def self_build_switch(settings: dict | None) -> str:
    """The separate 'Self-build loop: ON/OFF' toggle, next to the worker switch ('' when settings aren't known)."""
    if settings is None:
        return ""
    from .keeper_settings import self_build_state
    on = self_build_state(settings)
    return toggle(on, "setSelfBuild(this.checked)", "Self-build loop: ON" if on else "Self-build loop: OFF")


def render_persistent_tasks(settings: dict | None, now: datetime | None = None) -> str:
    """'Always-running tasks' card. An approved (running) persistent task shows 'Shut down task' where a
    one-off approval would show 'Change to No'; shutting down is not a rejection and touches no job."""
    if settings is None:
        return ""
    from .keeper_settings import VIA_SHUTDOWN, self_build_state
    on = self_build_state(settings)
    when = fmt_time(settings.get("self_build_changed_at"), now)
    who = settings.get("self_build_changed_by")
    how = "shut down" if settings.get("self_build_via") == VIA_SHUTDOWN else "switched " + ("on" if on else "off")
    last = (f"{esc(how)} {esc(when)}{' by ' + esc(who) if who else ''}" if when or who
            else '<span class="muted">never changed (on by default)</span>')
    if settings.get("problem"):
        last = esc(settings["problem"])
    if on:
        state = '<span class="badge yes">approved</span> <span class="badge working">Running</span>'
        control = (f'<button class="shutdown" onclick="shutDownTask(\'{SELF_BUILD_TASK}\')">Shut down task</button>'
                   '<span class="later">stops it at its next round; not a rejection</span>')
    else:
        state = '<span class="badge yes">approved</span> <span class="badge paused">Shut down</span>'
        control = ('<button class="startup" onclick="setSelfBuild(true)">Switch back on</button>'
                   '<span class="later">approvals are unaffected</span>')
    return ('<h2>Always-running tasks</h2><div class="card"><table><thead><tr><th>Task</th>'
            '<th style="width:200px">Status</th><th>Last change</th><th style="width:380px">Control</th></tr></thead>'
            f'<tbody><tr class="persistent" data-task="{SELF_BUILD_TASK}"><td><b>{esc(PERSISTENT_TASKS[SELF_BUILD_TASK])}</b> '
            f'<span class="chip">always running</span></td><td>{state}</td><td class="small">{last}</td>'
            f'<td><div class="actions" style="margin-top:0">{control}</div></td></tr></tbody></table></div>')


def brain_panel(info: dict | None) -> str:
    """Main brain switch for the header: current brain (live from .env), a Gemini / xAI toggle, each
    agent's resolved brain (pins marked) and what the worker's log last reported. No secrets."""
    if info is None:
        return ""
    from .brain_switch import CHOICES, LABELS
    worker = info.get("worker")
    reported = ""
    if worker:
        try:
            when = datetime.strptime(worker["at"], "%Y-%m-%d %H:%M:%S").strftime("%I:%M %p").lstrip("0")
        except (KeyError, ValueError):
            when = "?"
        reported = (f'<div class="reported">Keeper worker last reported ({esc(when)}): '
                    f'main brain {esc(LABELS.get(worker.get("main"), worker.get("main") or "?"))}</div>')
    if info.get("error"):
        return f'<div class="brain"><div class="row1">Main brain: {esc(info["error"])}</div>{reported}</div>'
    main = info.get("main") or ""

    def seg(fn, current, labels):
        return '<span class="seg">' + "".join(
            f'<button class="{"sel" if current == v else ""}" '
            f'onclick="{fn}(\'{v}\', \'{esc(label)}\', \'{esc(current)}\')">{esc(label)}</button>'
            for v, label in labels.items()) + "</span>"
    buttons = seg("setBrain", main, CHOICES)
    bezel = info.get("bezel") or ""
    bezel_names = {"xai": "Grok", "gemini": "Gemini"}
    source = " (config pin; no BRAIN_BEZEL line in .env)" if info.get("bezel_source") == "config pin" else ""
    bezel_row = ""
    if "bezel" in info:
        bezel_row = (f'<div class="row1">BEZEL&#39;s brain: <b>{esc(bezel_names.get(bezel, bezel or "not set"))}</b>'
                     f'{seg("setBezel", bezel, bezel_names)}<span class="small">{esc(source)}</span></div>')
    agents = " &middot; ".join(
        f'{esc(a["agent"])} {esc(a["brain"])}'
        + (' <span class="pin">(pinned)</span>' if a.get("pinned") else "")
        + (f' ({esc(a["note"])})' if a.get("note") else "")
        for a in info.get("agents") or [])
    return (f'<div class="brain"><div class="row1">Main brain: <b>{esc(LABELS.get(main, main or "not set"))}</b>'
            f'{buttons}</div>{bezel_row}'
            f'<div class="agents">{agents}</div>{reported}</div>')


def worker_banner(settings: dict | None) -> str:
    if settings is None or settings.get("worker_on"):
        return ""
    why = settings.get("problem") or "Switched off. The worker keeps running but won't pick up any job."
    return f'<div class="off-banner">Keeper worker is OFF. {esc(why)}</div>'


def render_job(job: dict, now: datetime | None = None, settings: dict | None = None,
               brain: dict | None = None) -> str:
    status = job_status(job)
    agents = ", ".join(agent_label(a) for a in env.as_list(job.get("agents"))) or "none yet"
    reopen = ""
    closing = env.closing_answer(job)
    if closing is not None and closing[1] == "no":
        reopen = (f'<div class="actions">{_change_buttons(job, closing[0])}'
                  f'<button class="reopen" onclick="reopenJob({esc(job.get("id"))})">Reopen</button>'
                  '<span class="later">Closed because you rejected it. Change to Yes to carry on, '
                  'or Reopen to answer again.</span></div>')
    elif closing is not None:
        reopen = (f'<div class="actions">{_change_buttons(job, closing[0])}'
                  f'<span class="later">Closed after you approved it. {esc(CHANGE_TO_NO_NOTE)}</span></div>')
    pause = ""
    if status != CLOSED:
        paused = env.is_paused(job)
        switch = toggle(not paused, "setPaused(%s, !this.checked)" % esc(job.get("id")),
                        "This job: Paused" if paused else "This job: On")
        pause = (f'<div class="actions">{switch}'
                 f'<span class="later">Switch off to have the Keeper worker skip this job. You can still answer approvals.</span></div>')
        if paused:
            pause = ('<div class="pause-banner">This job is paused. The Keeper worker skips it until you switch it back on.</div>'
                     + pause)
    body = (
        f'<div class="card pad"><div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px">'
        f'<span class="id">Job #{esc(job.get("id"))}</span>{badge(status)}{undo_chip(job, now)}'
        f'<span class="muted small">Created {esc(fmt_time(job.get("created_at"), now))} &middot; Agents: {esc(agents)}</span></div>'
        f'<div class="msg">{esc(job.get("message"))}</div>{reopen}{pause}</div>'
        f"<h2>Approvals</h2>{_render_approvals(job, now)}"
        f"<h2>Tasks</h2>{_render_tasks(job)}"
        f"<h2>Drafts</h2>{_render_drafts(job, now)}"
        f"<h2>Step log</h2>{_render_timeline(job, now)}")
    return page(f"Job #{job.get('id')}", worker_banner(settings) + body, short(job.get("message"), 140), back=True,
                switch=worker_switch(settings) + self_build_switch(settings) + brain_panel(brain))


def with_token(page_html: str, token: str) -> str:
    """Put this browser session's token into the page (a <meta> the page's JS sends back as
    X-Keeper-Token). Only the web server calls this; the token is never logged."""
    return page_html.replace("</head>", f'<meta name="keeper-token" content="{esc(token)}">\n</head>', 1)


def render_error(title: str, detail: str) -> str:
    body = f'<div class="problem"><h2>{esc(title)}</h2><div>{esc(detail)}</div></div>'
    return page("Keeper dashboard", body, "The page will try again every 10 seconds.")


def render_not_found(job_id) -> str:
    return render_error("Job not found", f"There is no job #{job_id}.")

