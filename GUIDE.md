# GUIDE: Set up the Cross Coat Matrix on your computer

**Purpose right now:** the Matrix's only job is to build itself. It does not run business or personal work yet. Future functions are parked until the Matrix is built. You are the only CEO: the AI drafts, and never sends, pays, deletes, or decides without your yes.

Written for Windows **Command Prompt**. Follow the steps in order.

---

## Part A: Python (once, already done on your PC)
1. Install **Python 3.14** with the **Python Install Manager** from https://www.python.org/downloads/ (or the Microsoft Store). Say yes to PATH / app aliases.
2. Keep the **App execution aliases** ON: Start → type `Manage app execution aliases` → make sure the Python entries are **On**. Turning them off makes `python` stop working.
3. Check: open Command Prompt and type `python --version`. ✅ You should see `Python 3.14.` and a number.

## Part B: Get into the project
4. The project is at `Desktop\cross-coat-matrix` (unzip `cross-coat-matrix.zip` to `C:\Users\Seabass\Desktop` if it's missing).
5. In Command Prompt type:
```
cd %USERPROFILE%\Desktop\cross-coat-matrix
```
✅ The prompt ends in `\Desktop\cross-coat-matrix>`.

## Part C: Install and check (once)
6. `pip install -r requirements.txt` ✅ Ends with `Successfully installed ...`. Yellow "not on PATH" warnings are harmless. If `pip` is not recognized: `python -m pip install -r requirements.txt`.
7. `pytest` ✅ All tests pass. (If not recognized: `python -m pytest`.)

## Part D: Talk to the Matrix
8. `python chat.py` ✅ You see `Cross Coat Matrix live chat (brain: ...)`. Type a message and watch the handoff log. If something would leave the system, it asks `Approve? (yes/no)`. Type `quit` to stop.

**Every time you come back:** `cd %USERPROFILE%\Desktop\cross-coat-matrix` then `python chat.py`.

## Part E: The AI brain (`.env` file)
Grok and Gemini are already connected. The `.env` file in the project folder picks which one is used.
- Open it: `notepad .env`
- Each line is plain `KEY=value`: no spaces around `=`, no quotes, **no comments on the same line**.
- Change one line to switch: `LLM_PROVIDER=xai` (Grok, `XAI_MODEL=grok-4.6`), `LLM_PROVIDER=gemini` (`GEMINI_MODEL=gemini-3.1-pro-preview`), or `LLM_PROVIDER=mock` (free, offline).
- Keys go **only** in `.env`. Never paste them into chat, email, or texts.

## Next
Go to **ROADMAP.md**: Zone 1 (create an agent) and Zone 2 (tools that help the Matrix build itself).

## If something goes wrong
- `'python' is not recognized` or the Microsoft Store opens → aliases must be ON (step 2), then open a **new** Command Prompt. Or type `py install --configure` and answer Y.
- `pip` / `pytest` not recognized → put `python -m` in front.
- `No module named ...` → run step 6 again inside the project folder.
- Gemini says 404 → use `GEMINI_MODEL=gemini-3.1-pro-preview` (not `gemini-2.5-pro`).
- `Unknown LLM_PROVIDER` or key errors → a `.env` line probably has a note on the same line, or the file saved as `.env.txt`.
- Anything else → copy the whole error and paste it into chat. Check no API key is showing first.
