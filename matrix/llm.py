"""Talks to the AI model. In mock mode it uses simple keyword rules so everything runs offline."""
from . import config, names

_llm = None

# The Matrix's purpose right now (keep in plain language; shown to the AI in every draft).
MATRIX_PURPOSE = ("The Matrix's only purpose right now is to build itself. It is not built yet, so it does not "
                  "run operations today (no job work, personal tasks, calendar, Gmail, invoicing/QuickBooks or "
                  "other operational work). Those are future functions, switched on later one at a time, only "
                  "with the owner's approval. ")


def get_llm():
    """Return a LangChain chat model, or None in mock mode."""
    global _llm
    if config.LLM_PROVIDER == "mock":
        return None
    if _llm is None:
        if config.LLM_PROVIDER == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI  # needs GOOGLE_API_KEY
            _llm = ChatGoogleGenerativeAI(model=config.GEMINI_MODEL, temperature=0)
        elif config.LLM_PROVIDER == "claude":
            from langchain_anthropic import ChatAnthropic  # needs ANTHROPIC_API_KEY
            _llm = ChatAnthropic(model=config.CLAUDE_MODEL, temperature=0)
        elif config.LLM_PROVIDER in ("xai", "grok"):
            from langchain_xai import ChatXAI  # needs XAI_API_KEY
            _llm = ChatXAI(model=config.XAI_MODEL, temperature=0)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}'. Use mock, gemini, claude or xai.")
    return _llm


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


def classify(text: str, table: dict[str, list[str]], default: str, instructions: str) -> str:
    """Ask the LLM to pick exactly one option; fall back to keywords if no LLM or a bad answer."""
    llm = get_llm()
    if llm is None:
        return keyword_pick(text, table, default)
    options = ", ".join(table)
    prompt = f"{instructions}\nOptions: {options}\nReply with ONE option name only.\n\nMessage: {text}"
    answer = _text(llm.invoke(prompt).content).upper()
    for name in table:
        if name in answer:
            return name
    return keyword_pick(text, table, default)


def write(role: str, text: str, mock_answer: str) -> str:
    """Let a specialist draft a short answer. Mock mode returns a canned answer."""
    llm = get_llm()
    if llm is None:
        return mock_answer
    name = names.assistant_name()  # set in the rename panel (default Stuart)
    prompt = (f"You are {name}, the assistant for Cross Coat Drywall (La Crete, Alberta; owner + 1-2 workers). Internally you are handling this as {role}, but never say that. Speak to the owner only as {name}, and never mention the Matrix, internal staff or specialist names (such as SUNDAY, STEWARD, BEZEL, FINISH or LUMEN), or which part of you handled the request. Do not introduce yourself unless asked. "
              "The owner is the only CEO; you are staff. You draft; you never send, pay, delete or decide "
              "without the owner's yes. " + MATRIX_PURPOSE +
              "Be brief and practical, use am/pm times. "
              f"Draft a response to: {text}")
    return _text(llm.invoke(prompt).content)
