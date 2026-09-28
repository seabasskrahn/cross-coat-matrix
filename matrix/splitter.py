"""SUNDAY's task splitter: turn one message into as FEW, meaty tasks as sensible.

Deterministic keyword rules (no LLM call), so splitting itself costs nothing:
  1. Break the message on new lines and semicolons (list bullets like "1." or "-" are stripped).
  2. Give each part a specialist hint from the keyword lists in matrix/agents.py.
  3. A part with no hint, or one that refers back ("it", "that", "they", "same", ...), joins the
     part before it: it shares that context.
  4. Parts with the same specialist hint are merged into one task (one draft call covers them).
  5. A separate task is only made for parts that need a DIFFERENT specialist.
  6. Hard cap (config.MAX_TASKS_PER_MESSAGE, default 3): over the cap, groups that report to the
     same senior staff member are merged (smallest first); a merged mixed group is drafted by that
     senior (STEWARD or BEZEL), and remembers which specialists it covers.
A part that says "after that" / "once ..." / "then" and starts a new task depends on the task before.

split() returns (tasks, notes). A multi-task split puts the specialist on each task
("agent": {"name", "role"}); a single task is left for the senior staff to assign as before.
"""
from __future__ import annotations

import re

from . import agents, config, llm

_SPLIT = re.compile(r"[\n;]+")
_BULLET = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s+")
_REFERS_BACK = re.compile(r"^(?:and\s+|but\s+)?(it|its|it's|that|this|these|those|them|they|their|same|"
                          r"which|there)\b", re.I)
_AFTER = re.compile(r"\b(after that|afterwards|once (?:that|they|it|those|these)|then)\b", re.I)

SPECIALIST_KEYWORDS = {name: words for name, (_job, words) in agents.SPECIALISTS.items()}
SENIOR_OF = {spec: senior for senior, specs in agents.DELEGATES.items() for spec in specs}


def parts_of(message: str) -> list[str]:
    parts = [_BULLET.sub("", p).strip() for p in _SPLIT.split(message or "")]
    return [p for p in parts if p] or [str(message or "").strip()]


def hint(part: str) -> str | None:
    """Specialist whose keywords best match this part, or None."""
    return llm.keyword_pick(part, SPECIALIST_KEYWORDS, "") or None


def _senior(agent: str) -> str | None:
    return agent if agent in agents.SENIOR_STAFF else SENIOR_OF.get(agent)


def split(message: str, cap: int | None = None) -> tuple[list[dict], list[dict]]:
    cap = max(1, cap or config.MAX_TASKS_PER_MESSAGE)
    parts = parts_of(message)
    # groups: {"agent": str|None, "parts": [(index, text)], "specialists": set, "after": bool}
    groups: list[dict] = []
    last = None  # group of the previous part
    for i, part in enumerate(parts):
        h = hint(part)
        if last is not None and (h is None or _REFERS_BACK.match(part)):
            last["parts"].append((i, part))
            continue
        same = next((g for g in groups if h is not None and g["agent"] == h), None)
        if same is None:
            same = {"agent": h, "parts": [], "specialists": {h} if h else set(),
                    "after": bool(_AFTER.search(part)) and bool(groups)}
            groups.append(same)
        same["parts"].append((i, part))
        last = same

    # A leading group with no specialist (e.g. "Hi, a few things:") joins the first real one.
    if len(groups) > 1 and groups[0]["agent"] is None:
        lead = groups.pop(0)
        groups[0]["parts"] = lead["parts"] + groups[0]["parts"]

    notes = []
    before_cap = len(groups)
    while len(groups) > cap:
        pairs = [(a, b) for a in range(len(groups)) for b in range(a + 1, len(groups))
                 if _senior(groups[a]["agent"]) and _senior(groups[a]["agent"]) == _senior(groups[b]["agent"])]
        if not pairs:  # nothing shares a senior: fold the last group into the one before it
            pairs = [(len(groups) - 2, len(groups) - 1)]
        a, b = min(pairs, key=lambda p: (len(groups[p[0]]["parts"]) + len(groups[p[1]]["parts"]),
                                         -(groups[p[0]]["agent"] in agents.SENIOR_STAFF
                                           or groups[p[1]]["agent"] in agents.SENIOR_STAFF)))
        ga, gb = groups[a], groups.pop(b)
        ga["parts"] = sorted(ga["parts"] + gb["parts"])
        ga["specialists"] |= gb["specialists"]
        if ga["agent"] != gb["agent"]:
            ga["agent"] = _senior(ga["agent"]) or ga["agent"]
        ga["after"] = ga["after"] and gb["after"]
    if before_cap > len(groups):
        notes.append({"tag": "tasks capped",
                      "detail": f"{before_cap} specialist groups -> {len(groups)} tasks "
                                f"(max {cap} per message)"})
    if len(parts) > len(groups):
        notes.append({"tag": "tasks merged", "detail": f"{len(parts)} parts -> {len(groups)} task(s)"})

    groups.sort(key=lambda g: g["parts"][0][0])
    multi = len(groups) > 1
    tasks = []
    for n, g in enumerate(groups, start=1):
        task = {"num": n, "title": "; ".join(p for _i, p in g["parts"]),
                "depends_on": [n - 1] if g["after"] and n > 1 else []}
        if multi and g["agent"]:
            task["agent"] = {"name": g["agent"], "role": agents.ROLE_LABELS.get(g["agent"], "agent")}
        if len(g["specialists"]) > 1:
            task["specialists"] = sorted(g["specialists"])
        tasks.append(task)
    return tasks, notes
