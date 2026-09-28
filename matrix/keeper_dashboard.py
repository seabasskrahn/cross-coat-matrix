"""Keeper dashboard: the logic behind keeper_dashboard.py (a local page to watch and answer jobs).

Kept separate from the web server so it can be tested offline:
- job_status(job)            -> "Working" | "Waiting on you" | "Closed" | "Halted"
- apply_answer(...)          -> the change one Approve / Reject / answer click makes to an approval
- answer_approval(store,...) -> saves that change (Postgres: one transaction with SELECT ... FOR UPDATE)
- render_list / render_job / render_error -> the HTML pages

The dashboard only ever changes the `status` (and answer text) of ONE pending approval entry in
`jobs.approvals`. The Keeper worker notices it on its next poll, logs `approval answered`, and
closes or continues the job. The dashboard never closes jobs and never sends anything.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config
from . import envelope as env

WORKING, WAITING, CLOSED, HALTED = "Working", "Waiting on you", "Closed", "Halted"
STATUSES = (WAITING, WORKING, HALTED, CLOSED)  # order of the summary strip
STATUS_CLASS = {WORKING: "working", WAITING: "waiting", CLOSED: "closed", HALTED: "halted"}
ANSWERED_BY = "owner (dashboard)"
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
    1. Closed          - step_log has a `job closed` tag
    2. Halted          - the LAST step_log tag is a halt tag (dependency cycle, circuit breaker, error...)
    3. Waiting on you  - any approval has status "pending"
    4. Working         - anything else (the worker will pick it up or is on it)
    """
    tags = env.tags(job)
    if env.JOB_CLOSED in tags:
        return CLOSED
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
def apply_answer(approvals, index: int, action: str, text: str = "", now: str | None = None) -> dict:
    """Validate one click and return the fields to merge into approvals[index].
    action: "yes" / "no" for a yes_no approval, "answer" (with text) for a context_request."""
    approvals = env.as_list(approvals)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(approvals):
        raise AnswerError("That approval doesn't exist.", 404)
    item = approvals[index]
    if not isinstance(item, dict):
        raise AnswerError("That approval entry is damaged.", 400)
    if env.status_of(item) != env.PENDING:
        raise AnswerError(f"That one was already answered ({env.status_of(item)}).", 409)
    changes = {"answered_at": now or env.utc_now(), "answered_by": ANSWERED_BY}
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


def answer_approval(store, job_id: int, index: int, action: str, text: str = "") -> dict:
    """Save one click. Returns the updated approval entry.
    PgStore: one transaction, the row locked with SELECT ... FOR UPDATE, only approvals[index]
    changed (jsonb_set). Any other store (MemoryStore, fakes): get + update_item."""
    if hasattr(store, "conn") and hasattr(store, "table"):
        return _answer_pg(store, job_id, index, action, text)
    job = store.get(job_id)
    if job is None:
        raise AnswerError("That job doesn't exist.", 404)
    changes = apply_answer(job.get("approvals"), index, action, text)
    store.update_item(job_id, "approvals", index, changes)
    return {**job["approvals"][index], **changes}


def _answer_pg(store, job_id: int, index: int, action: str, text: str) -> dict:
    from psycopg import sql
    from psycopg.types.json import Jsonb
    with store._mutex, store.conn.transaction():
        row = store.conn.execute(
            sql.SQL("SELECT approvals FROM {} WHERE id = %s FOR UPDATE").format(store.table), [job_id]).fetchone()
        if row is None:
            raise AnswerError("That job doesn't exist.", 404)
        changes = apply_answer(row["approvals"], index, action, text)
        new = store.conn.execute(
            sql.SQL("UPDATE {} SET approvals = jsonb_set(approvals, ARRAY[%s::text], (approvals -> %s) || %s) "
                    "WHERE id = %s RETURNING approvals -> %s AS item").format(store.table),
            [str(index), index, Jsonb(changes), job_id, index]).fetchone()
    return new["item"]


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
  .badge.pending { background:#fef3c7; color:#92400e; } .badge.yes, .badge.answered { background:#dcfce7; color:#166534; }
  .badge.no { background:#fee2e2; color:#991b1b; } .badge.other { background:#f3f4f6; color:#374151; }
  .strip { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:6px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 16px; min-width:140px; border-left-width:6px; }
  .stat b { display:block; font-size:26px; line-height:1.1; } .stat span { color:var(--muted); font-size:13px; }
  .stat.waiting { border-left-color:#f59e0b; } .stat.working { border-left-color:#3b82f6; }
  .stat.halted { border-left-color:#ef4444; } .stat.closed { border-left-color:#22c55e; } .stat.total { border-left-color:#6b7280; }
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
  #note { margin:0 0 14px; padding:10px 14px; border-radius:8px; display:none; white-space:pre-line; }
  #note.ok { display:block; background:#dcfce7; color:#166534; } #note.err { display:block; background:#fee2e2; color:#991b1b; }
  .problem { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:12px; padding:18px 20px; font-size:15px; }
  .problem h2 { margin:0 0 8px; }
"""

SCRIPT = r"""
function say(text, ok) { const m = document.getElementById("note"); if (!m) return; m.textContent = text; m.className = ok ? "ok" : "err"; window.scrollTo(0, 0); }
async function answer(job, index, action) {
  let text = "";
  if (action === "no" && !confirm("Reject this? The Keeper worker will stop this job.")) return;
  if (action === "answer") {
    text = document.getElementById("ans-" + index).value.trim();
    if (!text) { say("Type an answer first.", false); return; }
  }
  try {
    const r = await fetch("/api/answer", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({job:job, index:index, action:action, text:text})});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "Something went wrong");
    sessionStorage.setItem("keeper-note", "Saved. The Keeper worker will pick it up within about 10 seconds.");
    location.reload();
  } catch (e) { say("Not saved: " + e.message, false); }
}
const saved = sessionStorage.getItem("keeper-note");
if (saved) { sessionStorage.removeItem("keeper-note"); say(saved, true); }
// Refresh every 10 seconds, unless you're typing an answer.
setInterval(() => {
  const typing = [...document.querySelectorAll("textarea")].some(t => t.value.trim() || t === document.activeElement);
  if (!typing) location.reload();
}, REFRESH_MS);
"""


def page(title: str, body: str, subtitle: str = "", back: bool = False) -> str:
    nav = '<a href="/">&larr; All jobs</a>' if back else ""
    script = SCRIPT.replace("REFRESH_MS", str(REFRESH_SECONDS * 1000))
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)} - Keeper dashboard</title>\n<style>{STYLE}</style></head>\n<body>\n"
        f"<header><h1>{esc(title)}</h1>{nav}<p>{esc(subtitle)}</p></header>\n"
        f'<main><div id="note"></div>\n{body}\n</main>\n<script>{script}</script>\n</body></html>\n'
    )


def render_summary(counts: dict[str, int]) -> str:
    stats = "".join(f'<div class="stat {STATUS_CLASS[s]}"><b>{counts.get(s, 0)}</b><span>{esc(s)}</span></div>'
                    for s in STATUSES)
    total = sum(counts.values())
    return f'<div class="strip">{stats}<div class="stat total"><b>{total}</b><span>All jobs</span></div></div>'


def render_list(jobs: list[dict], now: datetime | None = None) -> str:
    rows = []
    for j in jobs:
        jid = j.get("id")
        agents = ", ".join(esc(agent_label(a)) for a in env.as_list(j.get("agents"))) or '<span class="muted">none yet</span>'
        rows.append(
            f'<tr class="row" onclick="location.href=\'/job/{esc(jid)}\'">'
            f'<td class="id">#{esc(jid)}</td>'
            f'<td><a class="job" href="/job/{esc(jid)}">{esc(short(j.get("message")))}</a></td>'
            f"<td>{badge(job_status(j))}</td>"
            f'<td style="text-align:center">{len(env.as_list(j.get("tasks")))}</td>'
            f"<td>{agents}</td>"
            f'<td style="white-space:nowrap">{esc(fmt_time(j.get("created_at"), now))}</td></tr>')
    table = ("".join(rows) if rows else
             '<tr><td colspan="6" class="muted" style="padding:18px">No jobs yet.</td></tr>')
    body = (render_summary(summary(jobs)) +
            '<h2>Jobs</h2><div class="card"><table><thead><tr><th style="width:60px">Job</th><th>Message</th>'
            '<th style="width:130px">Status</th><th style="width:60px">Tasks</th><th>Agents</th>'
            f'<th style="width:160px">Created</th></tr></thead><tbody>{table}</tbody></table></div>')
    tz = local_tz().key
    return page("Keeper dashboard", body,
                f"Every job Keeper is holding. Click a job to see its drafts and answer approvals. "
                f"Updates every {REFRESH_SECONDS} seconds. Times are {tz}.")


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


def _render_approvals(job: dict, now) -> str:
    items = env.as_list(job.get("approvals"))
    if not items:
        return '<div class="card pad muted">No approvals asked yet.</div>'
    jid = job.get("id")
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


def render_job(job: dict, now: datetime | None = None) -> str:
    status = job_status(job)
    agents = ", ".join(agent_label(a) for a in env.as_list(job.get("agents"))) or "none yet"
    body = (
        f'<div class="card pad"><div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px">'
        f'<span class="id">Job #{esc(job.get("id"))}</span>{badge(status)}'
        f'<span class="muted small">Created {esc(fmt_time(job.get("created_at"), now))} &middot; Agents: {esc(agents)}</span></div>'
        f'<div class="msg">{esc(job.get("message"))}</div></div>'
        f"<h2>Approvals</h2>{_render_approvals(job, now)}"
        f"<h2>Tasks</h2>{_render_tasks(job)}"
        f"<h2>Drafts</h2>{_render_drafts(job, now)}"
        f"<h2>Step log</h2>{_render_timeline(job, now)}")
    return page(f"Job #{job.get('id')}", body, short(job.get("message"), 140), back=True)


def render_error(title: str, detail: str) -> str:
    body = f'<div class="problem"><h2>{esc(title)}</h2><div>{esc(detail)}</div></div>'
    return page("Keeper dashboard", body, "The page will try again every 10 seconds.")


def render_not_found(job_id) -> str:
    return render_error("Job not found", f"There is no job #{job_id}.")

