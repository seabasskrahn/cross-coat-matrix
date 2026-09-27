# Cross Coat Matrix: Roadmap (next couple of days)

**Purpose right now:** the Matrix's only job is to build itself. It is not built yet, so it does not run any business or personal work today. Future functions are parked until the Matrix is built.

**Rule that never changes:** you (Seabass) are the only CEO. The AI drafts. It never sends, pays, deletes, or decides anything without your yes. API keys go only in the `.env` file, never in chat.

All commands below are typed in **Command Prompt**. Start every session with:
```
cd %USERPROFILE%\Desktop\cross-coat-matrix
```

---

## Zone 1: Create an AI agent inside the Matrix

Agents are defined in one file: `matrix\agents.py`. You do not need to touch `matrix\graph.py`. It reads `agents.py` and wires every agent in automatically (SUNDAY → STEWARD or BEZEL → agent → approval gate if needed).

1. Open the file: `notepad matrix\agents.py`
2. In the `SPECIALISTS` list, add one line for your new agent: a NAME in capitals, a one-line job, and a few keywords. Example:
   ```python
   "FORGE": ("builds the Matrix: plans new agents and drafts code for the owner to review",
             ["build", "agent", "code", "matrix", "feature"]),
   ```
3. In `DELEGATES`, add the name to one boss's list, e.g. `"STEWARD": ["MARGIN", "VECTOR", "FINISH", "FORGE"],`
4. Add the same keywords to that boss's list in `SENIOR_STAFF` (so SUNDAY sends matching messages to the right boss).
5. If the agent will ever change anything outside the chat (write files, save code, send), add it to `ALWAYS_OUTWARD` (e.g. `"FORGE": "write_code"`) so it always stops for your yes.
6. Save (Ctrl+S) and close Notepad.
7. Test it: `python chat.py`, then type `Build a new agent for the matrix`. You should see `STEWARD: delegated to FORGE`, then `Approve? (yes/no)` (because of step 5). Type `no`, then `quit` to stop.
8. Check nothing broke: `pytest` (all tests should pass).

Tips: keep names in CAPITALS and different from existing ones. Add one agent at a time and test after each.

---

## Zone 2: Keep building the Matrix itself (tools and APIs)

**Already connected:** Grok (xAI, `grok-4.6`) and Gemini (`gemini-3.1-pro-preview`). Both can write code, so no new AI model is needed. Switch with one line in `.env`: `LLM_PROVIDER=xai` or `LLM_PROVIDER=gemini`.

Add these one at a time, only when you're ready. Each one gets its own line in `.env` (`notepad .env`).

| Tool | What it helps the Matrix do | Cost (checked Sep 27, 2026) | Needed? |
|---|---|---|---|
| **GitHub** (fine-grained token) | Save its own code with full history, so every change can be undone. Make one **private** repo, and a token limited to only that repo with "Contents: Read and write". `.env`: `GITHUB_TOKEN=...` | Free account | **Recommended first** |
| **Tavily** search API | Research on the web (docs, how-tos) while it builds. `pip install langchain-tavily`. `.env`: `TAVILY_API_KEY=...` | 1,000 free credits/month, no credit card | Recommended |
| **Brave Search API** | Alternative to Tavily for web search | $5 per 1,000 searches, with $5 free credit each month. Card required; set the usage limit to $5 to stay free | Optional (pick Tavily *or* Brave) |
| **File tool** (LangChain FileManagementToolkit) | Read and write files, locked to one folder (e.g. `workspace\`) | Free (`pip install langchain-community`) | Recommended |
| **E2B** code sandbox | Run code the AI writes in a safe cloud machine, not on your PC. `.env`: `E2B_API_KEY=...` | Hobby plan $0, one-time $100 usage credit, no card to start | Optional |
| **Claude** (Anthropic) | A third brain. Already supported: `LLM_PROVIDER=claude` | Not checked | Optional |

Safety for Zone 2: anything that writes code, saves to GitHub, or runs code goes through the approval gate first. Never paste a key into chat. If a key leaks, delete it on the provider's site and make a new one.

Sources: GitHub tokens: docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens · Tavily: tavily.com/pricing, docs.tavily.com/documentation/api-credits, docs.langchain.com/oss/python/integrations/providers/tavily · Brave: api-dashboard.search.brave.com/documentation/pricing · File tool: docs.langchain.com/oss/python/integrations/tools/filesystem · E2B: e2b.dev/pricing

---

## Rename panel and 3D view (names)

Done: `python rename_panel.py` opens a local page to rename the assistant (default Stuart), agents and services, with a description, color and icon for each. Saved in `matrix_names.json` (committed; no secrets). Internal IDs never change.

Next: the 3D view reads `GET http://127.0.0.1:8765/names` (fields per entry: `id`, `kind`, `reports_to`, `display`, `description`, `color`, `icon`; services also have `status`: connected / planned / off). When a planned service gets connected, change its `status` in `matrix/names.py` (`SERVICES`).

