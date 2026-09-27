"""Display names the owner can change (via `python rename_panel.py`).

Internal ids (SUNDAY, STEWARD, TAPER, ...) never change and are what the code routes on.
Only the DISPLAY name, description, color and icon shown to the owner come from matrix_names.json.
Agents are read from matrix/agents.py, so a new agent shows up here automatically.
If matrix_names.json is missing or broken, the defaults below are used.
"""
import json
import os
import re
from pathlib import Path

from . import agents

ASSISTANT_ID = "ASSISTANT"   # the single voice the owner talks to (default display name: Stuart)
ROUTER_ID = "SUNDAY"
DEFAULT_ASSISTANT_NAME = "Stuart"
MAX_NAME = 40
MAX_DESCRIPTION = 200
MAX_ICON = 8
STATUSES = ("connected", "planned", "off")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "matrix_names.json"

# Look for the future 3D view. Anything not listed gets a color from the palette and a robot icon.
_LOOK = {
    ASSISTANT_ID: ("#4F8EF7", "🤵"), ROUTER_ID: ("#A855F7", "🧭"),
    "STEWARD": ("#0EA5E9", "🧠"), "BEZEL": ("#F97316", "⚙️"),
    "MARGIN": ("#22C55E", "💲"), "VECTOR": ("#6366F1", "📐"), "FINISH": ("#14B8A6", "✅"),
    "TAPER": ("#EAB308", "🦺"), "ARMOR": ("#64748B", "🛻"), "DEDUCT": ("#EF4444", "🧾"),
    "AUDIT": ("#10B981", "💵"), "LEDGER": ("#8B5CF6", "📒"),
}
_PALETTE = ["#F43F5E", "#06B6D4", "#84CC16", "#D946EF", "#F59E0B", "#3B82F6", "#EC4899", "#0D9488"]
_SENIOR_ROLES = {
    "STEWARD": "Senior staff: strategy, pricing, quality, takeoffs",
    "BEZEL": "Senior staff: day-to-day operations, crew, truck, money admin",
}

# Connected (and planned) outside services. id, display, description, status, color, icon.
SERVICES = [
    ("GROK", "Grok", "AI brain from xAI (LLM_PROVIDER=xai)", "connected", "#111827", "⚡"),
    ("GEMINI", "Gemini", "AI brain from Google (LLM_PROVIDER=gemini)", "connected", "#8E75FF", "✨"),
    ("CLAUDE", "Claude", "AI brain from Anthropic (LLM_PROVIDER=claude); unused option", "off", "#D97757", "🟠"),
    ("GITHUB", "GitHub", "Saves the Matrix's code with full history", "connected", "#24292F", "🐙"),
    ("TAVILY", "Tavily", "Web search for research", "connected", "#0EA5E9", "🔎"),
    ("FILE_TOOL", "File tool", "Reads and writes files in one locked folder", "connected", "#F59E0B", "📁"),
    ("N8N", "n8n", "Telegram + Slack workflows (built, not running yet)", "off", "#EA4B71", "🔗"),
    ("POSTGRES", "Postgres", "Optional: keeps paused approvals across restarts", "off", "#336791", "🐘"),
    ("TELEGRAM", "Telegram", "Phone chat and yes/no approvals", "planned", "#229ED9", "✈️"),
    ("GOOGLE", "Google (Gmail/Calendar/Drive)", "Email, calendar and files", "planned", "#EA4335", "📧"),
    ("QUICKBOOKS", "QuickBooks Online", "Bookkeeping", "planned", "#2CA01C", "📗"),
]
EDITABLE = ("display", "description", "color", "icon")


class NamesError(ValueError):
    """Raised when names fail validation. .errors holds plain-language messages."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def names_path() -> Path:
    """Where the names file lives. MATRIX_NAMES_FILE can point elsewhere (used by tests)."""
    return Path(os.environ.get("MATRIX_NAMES_FILE") or _DEFAULT_PATH)


def _agent_rows() -> list[tuple[str, str, str | None, str]]:
    """(id, kind, reports_to, default description) for every agent, read live from agents.py."""
    rows = [(ASSISTANT_ID, "assistant", None, "The one voice you talk to"),
            (ROUTER_ID, "router", None, "Switchboard: sends each message to the right senior staff")]
    for senior in agents.SENIOR_STAFF:
        rows.append((senior, "senior", ROUTER_ID, _SENIOR_ROLES.get(senior, "Senior staff")))
    boss = {spec: senior for senior, specs in agents.DELEGATES.items() for spec in specs}
    # Specialists in team order (each senior's DELEGATES list), then any not yet assigned to a senior.
    order = [s for s in boss if s in agents.SPECIALISTS] + [s for s in agents.SPECIALISTS if s not in boss]
    for spec in order:
        rows.append((spec, "specialist", boss.get(spec), agents.SPECIALISTS[spec][0]))
    return rows


def _look(entry_id: str, index: int) -> tuple[str, str]:
    return _LOOK.get(entry_id, (_PALETTE[index % len(_PALETTE)], "🤖"))


def defaults() -> dict:
    """Default names: assistant = Stuart, every agent = its internal id, services as listed above."""
    agent_list = []
    for i, (aid, kind, boss, desc) in enumerate(_agent_rows()):
        color, icon = _look(aid, i)
        agent_list.append({"id": aid, "kind": kind, "reports_to": boss,
                           "display": DEFAULT_ASSISTANT_NAME if aid == ASSISTANT_ID else aid,
                           "description": desc, "color": color, "icon": icon})
    services = [{"id": sid, "kind": "service", "status": status, "display": disp,
                 "description": desc, "color": color, "icon": icon}
                for sid, disp, desc, status, color, icon in SERVICES]
    return {"version": 1, "agents": agent_list, "services": services}


def _merge(data: dict) -> dict:
    """Lay saved editable fields over the defaults, matched by id. Ids not in code are dropped;
    agents added to agents.py since the last save get their defaults. kind, reports_to and a
    service's status always come from the code (SERVICES / agents.py), not from the file."""
    out = defaults()
    if not isinstance(data, dict):
        return out
    for section in ("agents", "services"):
        saved = {e.get("id"): e for e in data.get(section) or [] if isinstance(e, dict)}
        for entry in out[section]:
            mine = saved.get(entry["id"])
            if not mine:
                continue
            for field in EDITABLE:
                if field in mine and mine[field] is not None:
                    entry[field] = str(mine[field]).strip()
    return out


def validate(data: dict) -> list[str]:
    """Return a list of problems (empty list = OK)."""
    errors, seen = [], {}
    for entry in data["agents"] + data["services"]:
        name, eid = entry.get("display", ""), entry["id"]
        if not name:
            errors.append(f"{eid}: name can't be empty")
        elif len(name) > MAX_NAME:
            errors.append(f"{eid}: name is too long ({len(name)} characters, max {MAX_NAME})")
        elif _CONTROL.search(name):
            errors.append(f"{eid}: name has a hidden/control character")
        else:
            key = name.casefold()
            if key in seen:
                errors.append(f"'{name}' is used twice ({seen[key]} and {eid}); names must be different")
            seen[key] = eid
        if len(entry.get("description", "")) > MAX_DESCRIPTION:
            errors.append(f"{eid}: description is too long (max {MAX_DESCRIPTION})")
        if _CONTROL.search(entry.get("description", "")):
            errors.append(f"{eid}: description has a hidden/control character")
        color = entry.get("color", "")
        if color and not _COLOR.match(color):
            errors.append(f"{eid}: color must look like #RRGGBB (got '{color}')")
        if len(entry.get("icon", "")) > MAX_ICON:
            errors.append(f"{eid}: icon is too long (one emoji, max {MAX_ICON} characters)")
    return errors


def load() -> dict:
    """Current names (file merged over defaults). A missing, broken or invalid file = defaults."""
    path = names_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults()
    data = _merge(raw)
    return defaults() if validate(data) else data


def save(data: dict) -> dict:
    """Validate and write names. Raises NamesError with plain-language messages if something's wrong."""
    merged = _merge(data)
    errors = validate(merged)
    if errors:
        raise NamesError(errors)
    path = names_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return merged


def reset() -> dict:
    """Put every name back to the defaults (and save that)."""
    return save(defaults())


# ---------- helpers for owner-facing output ----------
def display_map() -> dict[str, str]:
    """{internal id: display name} for every agent and service."""
    data = load()
    return {e["id"]: e["display"] for e in data["agents"] + data["services"]}


def display_name(entry_id: str) -> str:
    """Display name for an internal id (falls back to the id itself)."""
    return display_map().get(entry_id, entry_id)


def assistant_name() -> str:
    """The name the owner talks to (default: Stuart)."""
    return display_name(ASSISTANT_ID)


def pretty(text: str) -> str:
    """Swap internal agent ids in a line (e.g. a routing log line) for their display names."""
    mapping = {k: v for k, v in display_map().items()
               if k != ASSISTANT_ID and k in {r[0] for r in _agent_rows()}}
    ids = sorted(mapping, key=len, reverse=True)
    if not ids:
        return str(text)
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, ids)) + r")\b")
    return pattern.sub(lambda m: mapping[m.group(1)], str(text))
