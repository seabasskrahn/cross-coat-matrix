"""All settings in one place. Values come from environment variables (or a .env file)."""
import os
from pathlib import Path

# Load a .env file if one exists next to this project (simple, no extra library needed).
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# "mock" = no API key, runs offline. "gemini", "claude" or "xai" (Grok) = real LLM.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")      # check current model names before use
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")      # check current model names before use
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.6")                     # check current model names at docs.x.ai

# ---------- Per-agent brains ----------
# Each agent can have its own brain: "provider" or "provider:model" (provider = xai, gemini or claude).
# Change one agent with ONE line, either here or in .env (the .env line wins), e.g.
#     BRAIN_STEWARD=xai              BRAIN_VECTOR=gemini:gemini-3.1-pro-preview
# Agents not listed use the default brain (LLM_PROVIDER + its model above). If an agent's key is
# missing, that agent quietly falls back to the default brain (a warning is logged). LLM_PROVIDER=mock
# is the master offline switch: every agent is mock and no AI is called.
AGENT_BRAINS = {
    "STEWARD": "xai",      # Grok
    "BEZEL": "gemini",     # Gemini
}
DEFAULT_MODELS = {"xai": XAI_MODEL, "grok": XAI_MODEL, "gemini": GEMINI_MODEL, "claude": CLAUDE_MODEL}
# Which .env key each provider needs (checked by name only; the value is never printed).
PROVIDER_KEYS = {"xai": ("XAI_API_KEY",), "grok": ("XAI_API_KEY",),
                 "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"), "claude": ("ANTHROPIC_API_KEY",)}


def agent_brain_setting(agent: str | None) -> str:
    """The raw brain setting for an agent ("" = use the default brain). .env beats AGENT_BRAINS."""
    if not agent:
        return ""
    return (os.getenv(f"BRAIN_{agent.upper()}") or AGENT_BRAINS.get(agent.upper(), "")).strip()


def has_key(provider: str) -> bool:
    return any(os.getenv(k, "").strip() for k in PROVIDER_KEYS.get(provider, ()))


# ---------- Scout (Tavily web search, read-only) ----------
# Used by the drafting step only when a task clearly needs outside facts. At most 1 search per task,
# at most SCOUT_MAX_SEARCHES_PER_JOB per job (0 = Scout off). Off entirely if TAVILY_API_KEY is missing.
SCOUT_MAX_SEARCHES_PER_JOB = int(os.getenv("SCOUT_MAX_SEARCHES_PER_JOB", "2"))
SCOUT_MAX_RESULTS = int(os.getenv("SCOUT_MAX_RESULTS", "3"))


def scout_key_present() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())

# Shared secret n8n must send in the X-Matrix-Key header. Empty = no check (only OK on your own computer).
MATRIX_API_KEY = os.getenv("MATRIX_API_KEY", "")
# Your Telegram user id. If set, /approve only accepts decisions from this id. You are the only CEO.
OWNER_TELEGRAM_ID = os.getenv("OWNER_TELEGRAM_ID", "")
# Postgres URL for saving paused approvals across restarts. Empty = keep in memory (lost on restart).
CHECKPOINT_DB_URL = os.getenv("CHECKPOINT_DB_URL", "")

TIMEZONE = os.getenv("TIMEZONE", "America/Edmonton")  # La Crete, Alberta
# Grace window: the Keeper worker waits this long after the owner answers an approval (yes, no or
# a written answer) before acting on it, so a mis-click can be undone. KEEPER_REJECT_GRACE_SECONDS
# is accepted as an older name for the same setting.
ANSWER_GRACE_SECONDS = float(os.getenv("KEEPER_ANSWER_GRACE_SECONDS",
                                       os.getenv("KEEPER_REJECT_GRACE_SECONDS", "30")))
MAX_TASKS_PER_SWEEP = 5          # Keeper worker: max tasks drafted per job run (circuit breaker)
MAX_TASKS_PER_MESSAGE = int(os.getenv("MAX_TASKS_PER_MESSAGE", "3"))  # splitter hard cap per message

# Existing relay convention (for when the Gmail relay is wired up later).
SUBJECT_TASK = "[MERLIN-TASK]"
SUBJECT_LOG = "[MERLIN-LOG]"
GMAIL_TASK_SEARCH = "subject:(MERLIN-TASK)"  # Gmail search must NOT use the square brackets
