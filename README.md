# Cross Coat Matrix

## Purpose (right now)
The Matrix's only purpose right now is to **build itself**. It is not built yet, so it does not run any operations today: no drywall or job work, no personal tasks, no calendar, no reading Gmail, no invoicing or QuickBooks, no other operational work.

**Future functions are parked until the Matrix is built.** They get switched on later, one at a time, only with the owner's approval.

**You (Seabass) are the only CEO.** The AI drafts. It never sends, pays, deletes, or decides without your yes.

## What it has so far
- A switchboard (**S.U.N.D.A.Y.**) that passes each message to a boss (**S.T.E.W.A.R.D.** or **B.E.Z.E.L.**), who hands it to an agent.
- An **approval gate**: anything that would leave the system stops and asks you yes or no.
- A log of every handoff (am/pm times), max 5 tasks at a time.
- Two AI brains connected: Grok (`grok-4.6`) and Gemini (`gemini-3.1-pro-preview`). `mock` mode runs offline for free.

The specialist agents already listed in `matrix/agents.py` are placeholders for **future functions (not active)**. They only say what they would do.

## Run it (Windows Command Prompt)
```
cd %USERPROFILE%\Desktop\cross-coat-matrix
pytest
python chat.py
```
First time only: `pip install -r requirements.txt`. Type `quit` to leave the chat.

**New to this? See [GUIDE.md](GUIDE.md). What to build next: [ROADMAP.md](ROADMAP.md).**

## Main files
| File | What it does |
|---|---|
| `chat.py` | Talk to the Matrix in the terminal |
| `rename_panel.py` | Rename panel: change names shown for the assistant, agents and services (saved in `matrix_names.json`) |
| `matrix/names.py` | Loads/saves/checks the names in `matrix_names.json` |
| `matrix/agents.py` | Who's who. **Where you add new agents** (see ROADMAP Zone 1) |
| `matrix/graph.py` | Wires the agents together automatically from `agents.py` |
| `matrix/llm.py` | Talks to Grok, Gemini or Claude (or mock mode) |
| `matrix/config.py` | Settings, read from `.env` |
| `tests/test_smoke.py` | Automatic checks (`pytest`) |
| `demo.py`, `matrix/api.py`, `n8n/`, `docker-compose.yml`, `Dockerfile`, `postgres-init/` | Built earlier for a later server/phone setup. Not needed now |

## Rename panel (change what things are called)
```
python rename_panel.py
```
Your browser opens a page (http://127.0.0.1:8765, only reachable from this computer) listing your assistant, the agents, and the services. Change a name, description, color or icon, then press **Save**. **Reset to defaults** puts everything back (Stuart, SUNDAY, STEWARD, ...). Press Ctrl+C in the Command Prompt window to close the panel.

- Names are saved in `matrix_names.json`. If that file is missing, the defaults are used.
- The first row is the name you talk to: `chat.py` greets you with it, answers with it, and asks for your yes with it. Agent names show in the routing lines (`SHOW_ROUTING=1`).
- Only the names on screen change. Inside, the Matrix still uses the fixed IDs (SUNDAY, TAPER, ...), so nothing breaks. Rules: 1 to 40 characters, no two the same.
- New agents added to `matrix/agents.py` show up in the panel automatically.
- The future 3D view can read the same data as JSON at http://127.0.0.1:8765/names.

## Switching the AI brain
In `.env` (plain `KEY=value` lines, no comments on the same line), change one line: `LLM_PROVIDER=xai`, `LLM_PROVIDER=gemini`, or `LLM_PROVIDER=mock`. Keys go only in `.env`. Never paste them into chat.

## Tested
Tested 2026-09-27 on Linux (Python 3.12/3.13) and set up on Windows with Python 3.14. Not tested on a Mac.
