"""Talks to the AI model. In mock mode it uses simple keyword rules so everything runs offline."""
import logging

from . import config, names

log = logging.getLogger("matrix.llm")

_llms: dict[tuple[str, str], object] = {}   # one chat model per (provider, model), shared by agents
_warned: set[tuple] = set()

# The Matrix's purpose right now (keep in plain language; shown to the AI in every draft).
MATRIX_PURPOSE = ("The Matrix's only purpose right now is to build itself. It is not built yet, so it does not "
                  "run operations today (no job work, personal tasks, calendar, Gmail, invoicing/QuickBooks or "
                  "other operational work). Those are future functions, switched on later one at a time, only "
                  "with the owner's approval. ")


def _norm(provider: str) -> str:
    provider = (provider or "").strip().lower()
    return "xai" if provider == "grok" else provider


def default_brain() -> tuple[str, str]:
    """(provider, model) of the default brain: LLM_PROVIDER in .env ("mock" = offline)."""
    provider = _norm(config.LLM_PROVIDER)
    return provider, config.DEFAULT_MODELS.get(provider, "")


def _warn_once(key: tuple, msg: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


def brain_for(agent: str | None = None) -> tuple[str, str]:
    """(provider, model) this agent thinks with. Set per agent in config.AGENT_BRAINS or with a
    BRAIN_<AGENT>=provider[:model] line in .env. Falls back to the default brain (with a logged
    warning, never a crash) if the setting is unknown or that provider's key is missing."""
    default = default_brain()
    if default[0] == "mock":
        return default  # master offline switch: no agent calls an AI
    setting = config.agent_brain_setting(agent)
    if not setting:
        return default
    provider, _, model = setting.partition(":")
    provider, model = _norm(provider), model.strip()
    if provider == "mock":
        return "mock", ""
    if provider not in config.DEFAULT_MODELS:
        _warn_once((agent, "unknown", provider), "brain for %s: unknown provider '%s'; using the default "
                   "brain (%s)", agent, provider, default[0])
        return default
    if not config.has_key(provider):
        _warn_once((agent, "nokey", provider), "brain for %s: no API key for %s in .env; using the default "
                   "brain (%s)", agent, provider, default[0])
        return default
    return provider, model or config.DEFAULT_MODELS[provider]


def brain_label(agent: str | None = None) -> str:
    provider, model = brain_for(agent)
    return f"{provider}:{model}" if model else provider


def _make(provider: str, model: str):
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI  # needs GOOGLE_API_KEY
        return ChatGoogleGenerativeAI(model=model, temperature=0)
    if provider == "claude":
        from langchain_anthropic import ChatAnthropic  # needs ANTHROPIC_API_KEY
        return ChatAnthropic(model=model, temperature=0)
    if provider == "xai":
        from langchain_xai import ChatXAI  # needs XAI_API_KEY
        return ChatXAI(model=model, temperature=0)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use mock, gemini, claude or xai.")


def get_llm(agent: str | None = None):
    """Return the LangChain chat model for this agent (default brain if agent is None), or None in
    mock mode. If an agent's own brain can't be built, it falls back to the default brain."""
    provider, model = brain_for(agent)
    if provider == "mock":
        return None
    key = (provider, model)
    if key not in _llms:
        default = default_brain()
        try:
            _llms[key] = _make(provider, model)
        except Exception as exc:  # noqa: BLE001 - e.g. a package or setting problem for one agent's brain
            if key == default:
                raise
            _warn_once((agent, "build", provider), "brain for %s (%s) could not start (%s); using the default "
                       "brain (%s)", agent, provider, type(exc).__name__, default[0])
            return get_llm(None)
    return _llms[key]


def _text(content) -> str:
    """Newer models (e.g. Gemini 3) return a list of content blocks; keep only the plain text."""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts).strip()
    return str(content).strip()


def keyword_pick(text: str, table: dict[str, list[str]], default: str) -> str:
    """Pick the option whose keywords appear most in the text (mock-mode classifier)."""
    low = text.lower()
    scores = {name: sum(1 for w in words if w in low) for name, words in table.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else default


def classify(text: str, table: dict[str, list[str]], default: str, instructions: str,
             agent: str | None = None) -> str:
    """Ask the LLM (this agent's brain) to pick exactly one option; fall back to keywords if no LLM or
    a bad answer."""
    llm = get_llm(agent)
    if llm is None:
        return keyword_pick(text, table, default)
    options = ", ".join(table)
    prompt = f"{instructions}\nOptions: {options}\nReply with ONE option name only.\n\nMessage: {text}"
    answer = _text(llm.invoke(prompt).content).upper()
    for name in table:
        if name in answer:
            return name
    return keyword_pick(text, table, default)


def write(role: str, text: str, mock_answer: str, agent: str | None = None) -> str:
    """Let a specialist draft a short answer with its own brain. Mock mode returns a canned answer."""
    llm = get_llm(agent)
    if llm is None:
        return mock_answer
    name = names.assistant_name()  # set in the rename panel (default Stuart)
    prompt = (f"You are {name}, the assistant for Cross Coat Drywall (La Crete, Alberta; owner + 1-2 workers). Internally you are handling this as {role}, but never say that. Speak to the owner only as {name}, and never mention the Matrix, internal staff or specialist names (such as SUNDAY, STEWARD, BEZEL, FINISH or LUMEN), or which part of you handled the request. Do not introduce yourself unless asked. "
              "The owner is the only CEO; you are staff. You draft; you never send, pay, delete or decide "
              "without the owner's yes. " + MATRIX_PURPOSE +
              "Be brief and practical, use am/pm times. "
              f"Draft a response to: {text}")
    return _text(llm.invoke(prompt).content)
