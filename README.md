# Cross Coat Matrix

A multi-agent AI system for Cross Coat Drywall. For now its only job is to **build itself**:
it does no real operations yet (no invoicing, email, calendar or job work). Those get switched on
later, one at a time, with the owner's approval.

**Seabass is the only CEO.** The AI drafts. It never sends, pays, deletes or decides without his yes.

## How it works

- **SUNDAY** (switchboard) reads each message, splits it into a small number of numbered tasks,
  and passes them to a senior: **STEWARD** (Grok brain) or **BEZEL** (Gemini brain).
- The senior assigns each task to a specialist agent (name + role). The specialist writes a **draft**.
- Anything that would leave the system stops at an **approval** and waits for Seabass.
  LEDGER always stops.
- **Keeper** is the working memory. Every job is saved to Postgres as it happens, so nothing is
  lost when the program closes.

## Changing an agent's brain

Each agent can think with its own AI brain. Out of the box:

| Agent | Brain |
| --- | --- |
| STEWARD | Grok (`xai`, `XAI_MODEL`, default grok-4.6) |
| BEZEL | Gemini (`gemini`, `GEMINI_MODEL`) |
| Everyone else (SUNDAY and all specialists) | The default brain: `LLM_PROVIDER` in `.env` |

**Main brain switch.** The first setting in `.env` is the main brain:
```
# ===== MAIN BRAIN: change to gemini or xai =====
LLM_PROVIDER=gemini
```
It sets the brain for every agent without its own `BRAIN_<AGENT>` line. STEWARD (xai) and BEZEL
(gemini) are pinned in `matrix/config.py` and don't follow it. The Keeper worker re-reads `.env`
at the start of every sweep, so a change takes effect within about 10 seconds with no restart. The
log shows `main brain changed: gemini -> xai`. A typo keeps the previous brain and logs a warning.

To change one agent, add **one line** to `.env` (picked up on the next sweep, too):
```
BRAIN_TAPER=xai
BRAIN_VECTOR=gemini:gemini-3.1-pro-preview
```
The format is `BRAIN_<AGENT>=provider` or `provider:model`, where provider is `xai` (Grok),
`gemini` or `claude`. You can also change the built-in defaults in `AGENT_BRAINS` in
`matrix/config.py` (a `.env` line wins over it). If an agent's key is missing (for example no
`XAI_API_KEY`), that agent uses the default brain and a warning goes in the log. It never crashes.
`LLM_PROVIDER=mock` is the offline switch: then every agent is mock and no AI is called. The
worker's log lists each agent's brain when it starts.

## Scout (web search)

Scout is a **read-only** Tavily web search the drafting step can use when a task clearly needs
outside facts, like "look up", "research", "latest", "current price", "building code" or
"release notes". It only reads the web, so it needs no approval. The drafts it feeds still wait for
your yes, as before.

- **At most 1 search per task**, and at most `SCOUT_MAX_SEARCHES_PER_JOB` per job (default 2; set
  it to `0` in `.env` to turn Scout off). Each search is a basic search with 3 results
  (`SCOUT_MAX_RESULTS`).
- **Off entirely** without `TAVILY_API_KEY` in `.env`, and in mock mode.
- Every search shows in the job's `step_log` as `scout search` (task, agent, query, number of
  results), and the draft it fed carries `"scout": {"query": ...}`. Only the query is saved, never
  the key. A failed search is logged, and the agent drafts without it.

## Keeper in one minute

Each job is one row in the `jobs` table (database `keeper`) with six fields:

| Field | Holds |
| --- | --- |
| `message` | What was asked |
| `tasks` | Numbered tasks with dependencies, each with agent name + role |
| `agents` | Who's assigned |
| `drafts` | Each agent's output, saved as-is |
| `approvals` | A back-and-forth thread (quick yes/no or context request). The job waits until it's resolved |
| `step_log` | Timestamped actions with tags (`message received`, `draft saved`, `approval asked`, `job closed`, ...) |

The full spec is in **[docs/KEEPER_SPEC.md](docs/KEEPER_SPEC.md)**.

A background **Keeper worker** checks `jobs` every 10 seconds, drafts any pending job, asks for
approval, and stops. It never approves on its own, and a circuit breaker caps tasks and brain calls
per round.

## Quick start (Windows)

**1. Python packages** (first time only)
```
cd %USERPROFILE%\Desktop\cross-coat-matrix
pip install -r requirements.txt
```

**2. Postgres for Keeper.** Follow **[docs/KEEPER_SETUP.md](docs/KEEPER_SETUP.md)**: install
PostgreSQL 17, create the `keeper` database and a `matrix` login, then add this line to `.env`:
```
CHECKPOINT_DB_URL=postgresql://matrix:YOUR_PASSWORD@localhost:5432/keeper
```
Without this line the Matrix still runs, but it forgets everything when it stops.

**3. Brain.** In `.env`, set `LLM_PROVIDER=xai` (Grok), `gemini`, or `mock` (free, offline), plus
the matching API key. Keys go only in `.env`, never in chat. See `.env.example`.

**4. Check it**
```
python -m pytest
```

**5. Talk to the Matrix**
```
python chat.py
```
Type `quit` to leave.

**6. Run the Keeper worker**
```
run_keeper.bat
```
It runs hidden in the background and writes to `logs\keeper_worker.log`. To stop it:
```
powershell -Command "Stop-Process -Id (Get-Content logs\keeper_worker.pid)"
```
Use `python -m matrix.keeper_worker --once` for a single sweep. You can view and answer jobs in
pgAdmin 4 (set an approval's `status` to `yes` or `no`).

## Keeper dashboard

```
run_dashboard.bat
```
(or `python keeper_dashboard.py`) opens a local page at http://127.0.0.1:8766 showing every Keeper
job: a count per status at the top, then each job with a badge (Working, Waiting on you, Closed,
Halted), its tasks, agents and time. Click a job to see its tasks, drafts, approval thread and step
log. It updates every 10 seconds, with times in am/pm local time.

On a pending approval, **Approve** or **Reject** (or type an answer to a question) saves your answer
on that approval only. The Keeper worker picks it up within 10 seconds and closes or continues the
job. The dashboard never sends anything. Close the window (or press Ctrl+C) to stop it.

**Mis-click?** After any answer you have 30 seconds (`KEEPER_ANSWER_GRACE_SECONDS` in `.env`)
to press **Undo**, or to flip it with **Change to Yes** / **Change to No**. The worker waits until
then. After that, a job your Reject closed shows **Change to Yes** and **Reopen** (ask me again),
and a job you approved shows **Change to No**. That only records the change and marks the job
rejected. Drafts already made stay as they are, and nothing was ever sent.

**On/Off switches.** The switch at the top turns the Keeper worker On or Off. When it's Off, the
worker keeps running but picks up nothing (saved in `logs\keeper_settings.json`). Each open job
also has its own switch: a paused job is skipped until you switch it back on, and you can still
answer its approvals while it's paused.

## Working with Grok Bot

Seabass builds the Matrix by talking to **Grok Bot**, his AI assistant, by chat or voice call.
Grok Bot edits this repo, runs the tests, pushes to GitHub, writes the docs in `docs/`, and can run
commands on Seabass's PC with his approval. The same rules apply: he's the CEO, Grok Bot drafts
and builds, and nothing goes out, gets paid or gets deleted without his yes.

## Other tools

- `python rename_panel.py` opens a local page (http://127.0.0.1:8765) to rename the assistant,
  agents and services. The names are saved in `matrix_names.json`.
- `matrix/agents.py` is the roster, and new agents go there.
- `docker-compose.yml`, `Dockerfile`, `n8n/`, `postgres-init/` and `matrix/api.py` are for a later
  server/phone setup and aren't needed now.

## More docs

[GUIDE.md](GUIDE.md) (new to this) · [ROADMAP.md](ROADMAP.md) · [docs/TIMELINE.md](docs/TIMELINE.md) ·
[docs/MIND_MAP.md](docs/MIND_MAP.md) · [docs/MIND_MAP_SIMPLE.md](docs/MIND_MAP_SIMPLE.md)
