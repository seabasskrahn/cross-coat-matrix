"""Keeper switches shared by the dashboard and the worker: a small JSON file, logs/keeper_settings.json.

    {"worker_on": true, "changed_at": "2026-09-28T01:40:00Z", "changed_by": "owner (dashboard)"}

- No file = worker On (the default, same as before this switch existed).
- An unreadable / damaged file = Off (the safe side), and the dashboard says so.
- KEEPER_SETTINGS_FILE points somewhere else (tests use a temp file).
Written atomically (temp file + rename), so a reader never sees half a file.
When the worker is Off it keeps running but claims no jobs.
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


def set_worker_on(on: bool, by: str = "owner (dashboard)") -> dict:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in load().items() if k != "problem"}
    data.update(worker_on=bool(on), changed_at=utc_now(), changed_by=by)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return data
