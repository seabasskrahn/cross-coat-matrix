"""All settings in one place. Values come from environment variables (or a .env file)."""
import logging
import os
from pathlib import Path

log = logging.getLogger("matrix.config")

# The brains LLM_PROVIDER (the MAIN BRAIN line at the top of .env) may name. "mock" = offline, no AI.
VALID_PROVIDERS = ("mock", "gemini", "xai", "grok", "claude")

_env_file = Path(__file__).resolve().parent.parent / ".env"
_file_keys: set[str] = set()   # keys the .env file put into the environment (so a removed line is undone)


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """KEY=value pairs from .env (python-dotenv if installed, else a small parser). Never logged."""
    path = Path(path or _env_file)
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values
        return {k: v for k, v in dotenv_values(path, encoding="utf-8", interpolate=False).items() if k and v is not None}
    except ImportError:
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
        return values


def _valid_provider(value, fallback: str) -> str:
    value = str(value or "").strip().lower()
    if value in VALID_PROVIDERS:
        return value
    log.warning("LLM_PROVIDER '%s' is not valid (use %s); keeping %s", value, ", ".join(VALID_PROVIDERS), fallback)
    return fallback


def _int_env(name: str, current: int) -> int:
    try:
        return int(os.getenv(name, str(current)))
    except ValueError:
        log.warning("%s must be a whole number; keeping %s", name, current)
        return current


# Load .env once at start-up. Variables already set in the environment win (tests rely on this).
for _k, _v in read_env_file().items():
    if _k not in os.environ:
        os.environ[_k] = _v
        _file_keys.add(_k)

# MAIN BRAIN. "mock" = no API key, runs offline. "gemini", "claude" or "xai" (Grok) = real LLM.
# The Keeper worker re-reads .env every sweep (reload_env below), so a change needs no restart.
LLM_PROVIDER = _valid_provider(os.getenv("LLM_PROVIDER", "mock"), "mock")
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
SCOUT_MAX_SEARCHES_PER_JOB = _int_env("SCOUT_MAX_SEARCHES_PER_JOB", 2)
SCOUT_MAX_RESULTS = _int_env("SCOUT_MAX_RESULTS", 3)


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


def reload_env(path: Path | None = None) -> bool:
    """Re-read .env (its values win, like load_dotenv(override=True)) and refresh the settings that
    can change live: the main brain, model names, per-agent BRAIN_<AGENT> lines and the Scout cap.
    A line removed from .env is removed from the environment too. An invalid LLM_PROVIDER keeps the
    previous brain (warning logged). Returns True if the main brain changed. Secrets are never logged."""
    global LLM_PROVIDER, GEMINI_MODEL, CLAUDE_MODEL, XAI_MODEL, SCOUT_MAX_SEARCHES_PER_JOB, SCOUT_MAX_RESULTS
    values = read_env_file(path)
    for gone in _file_keys - set(values):
        os.environ.pop(gone, None)
    _file_keys.clear()
    for k, v in values.items():
        os.environ[k] = v
        _file_keys.add(k)
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
    XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.6")
    DEFAULT_MODELS.update({"xai": XAI_MODEL, "grok": XAI_MODEL, "gemini": GEMINI_MODEL, "claude": CLAUDE_MODEL})
    SCOUT_MAX_SEARCHES_PER_JOB = _int_env("SCOUT_MAX_SEARCHES_PER_JOB", SCOUT_MAX_SEARCHES_PER_JOB)
    SCOUT_MAX_RESULTS = _int_env("SCOUT_MAX_RESULTS", SCOUT_MAX_RESULTS)
    old = LLM_PROVIDER
    LLM_PROVIDER = _valid_provider(os.getenv("LLM_PROVIDER", old), old)
    if LLM_PROVIDER != old:
        log.info("main brain changed: %s -> %s", old, LLM_PROVIDER)
        return True
    return False
