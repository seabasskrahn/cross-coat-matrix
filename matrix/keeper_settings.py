"""Keeper switches shared by the dashboard and the worker: a small JSON file, logs/keeper_settings.json.

    {"worker_on": true, "changed_at": "2026-09-28T01:40:00Z", "changed_by": "owner (dashboard)"}

- No file = worker On (the default, same as before this switch existed).
- An unreadable / damaged file = Off (the safe side), and the dashboard says so.
- KEEPER_SETTINGS_FILE points somewhere else (tests use a temp file).
Written atomically (temp file + rename), so a reader never sees half a file.
When the worker is Off it keeps running but claims no jobs.

The same file also holds the separate self-build loop switch (the dashboard's "Self-build loop: ON/OFF"
toggle and its "Shut down task" button). It has its own metadata keys so the worker switch's
changed_at / changed_by are never overwritten:

    {"self_build_on": false, "self_build_changed_at": "...", "self_build_changed_by": "owner (dashboard)",
     "self_build_via": "shut down task"}

- Missing key (or no file) = self-build loop ON. A damaged file / non-boolean value = OFF (the safe side).
- External loops call is_self_build_on() once per round.
- Flipping it never touches any job or approval; it only rewrites this file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .envelope import utc_now

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "logs" / "keeper_settings.json"


def settings_file() -> Path:
    return Path(os.getenv("KEEPER_SETTINGS_FILE") or DEFAULT_FILE)


def load() -> dict:
    """Current settings. Adds "problem" (text) if the file couldn't be read."""
    path = settings_file()
    if not path.exists():
        return {"worker_on": True}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("worker_on", True), bool):
            raise ValueError("unexpected content")
        return {"worker_on": True, **data}
    except (OSError, ValueError) as exc:
        return {"worker_on": False, "problem": f"{path.name} is unreadable ({type(exc).__name__}); treating it as Off"}


def worker_on() -> bool:
    return bool(load().get("worker_on"))


def _write(data: dict) -> dict:
    """The one safe writer for this file: temp file + atomic rename, so a reader never sees half a file."""
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return data


def set_worker_on(on: bool, by: str = "owner (dashboard)") -> dict:
    data = {k: v for k, v in load().items() if k != "problem"}
    data.update(worker_on=bool(on), changed_at=utc_now(), changed_by=by)
    return _write(data)


# ---------- self-build loop switch ----------
SELF_BUILD_KEY = "self_build_on"
VIA_TOGGLE, VIA_SHUTDOWN = "toggle", "shut down task"


class SettingsDamaged(OSError):
    """The settings file is unreadable, so a switch can't be saved without losing what's in it."""


def self_build_state(settings: dict | None) -> bool:
    """self_build_on from an already-loaded settings dict (see load()): missing = ON, damaged = OFF."""
    if settings is None:
        return True
    if settings.get("problem"):
        return False
    value = settings.get(SELF_BUILD_KEY, True)
    return value if isinstance(value, bool) else False


def is_self_build_on(settings_path: str | os.PathLike | None = None) -> bool:
    """Should the self-build loop run this round? Call it once per round.

    Reads `self_build_on` from logs/keeper_settings.json (or settings_path / KEEPER_SETTINGS_FILE).
    Returns True when the file or the key is missing (the default is ON), the boolean when it's set,
    and False when the file is unreadable or the value isn't a boolean (the safe side). Never raises
    for a bad file and never writes anything.
    """
    path = Path(settings_path) if settings_path is not None else settings_file()
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return self_build_state(data)


def set_self_build_on(on: bool, by: str = "owner (dashboard)", via: str = VIA_TOGGLE) -> dict:
    """Write self_build_on (+ self_build_changed_at / _by / _via) with the same safe writer as the
    worker switch. Other keys (worker_on, its changed_at / changed_by, ...) are kept as they are.
    Refuses (SettingsDamaged) when the file is unreadable, rather than overwrite it."""
    current = load()
    if current.get("problem"):
        raise SettingsDamaged(current["problem"])
    data = {k: v for k, v in current.items() if k != "problem"}
    data.update({SELF_BUILD_KEY: bool(on), "self_build_changed_at": utc_now(),
                 "self_build_changed_by": by, "self_build_via": via})
    return _write(data)


def shut_down_self_build(by: str = "owner (dashboard)") -> dict:
    """'Shut down task' for the self-build loop: self_build_on -> false, logging who and when.
    Not a rejection: no job or approval is touched."""
    return set_self_build_on(False, by=by, via=VIA_SHUTDOWN)
