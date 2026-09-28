"""The dashboard's MAIN BRAIN switch: read the LLM_PROVIDER line in .env live, and rewrite ONLY
that line (backup first, atomic replace). The Keeper worker re-reads .env every sweep, so a change
takes effect within about 10 seconds with no restart.

Safety:
- Only "gemini" or "xai" can be set from here (mock is not offered).
- Every other byte of .env (comments, keys, blank lines, line endings) is kept exactly.
- .env is backed up to logs/.env.bak-<timestamp> before the change.
- No value from .env other than LLM_PROVIDER (and model names) is ever returned, shown or logged.
  Keys are only checked for presence.
Paths can be pointed elsewhere (tests use temp files): KEEPER_ENV_FILE, KEEPER_BACKUP_DIR,
KEEPER_WORKER_LOG.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import config

CHOICES = {"gemini": "Gemini", "xai": "xAI (Grok)"}
LABELS = {**CHOICES, "grok": "xAI (Grok)", "claude": "Claude", "mock": "mock (offline)"}
_BRAINS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[^\r\n]*? brains: ([^\r\n]*?)\r?$", re.M)
MAIN_KEY, BEZEL_KEY = "LLM_PROVIDER", "BRAIN_BEZEL"


def _line_re(key: str) -> re.Pattern:
    """KEY=value line; group 2 is the value only (spaces and stray carriage returns are kept)."""
    return re.compile(rb"^([ \t]*" + re.escape(key.encode()) + rb"[ \t]*=[ \t]*)([^\r\n]*?)([ \t]*)\r*$", re.M)


class BrainSwitchError(ValueError):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def env_path() -> Path:
    return Path(os.environ.get("KEEPER_ENV_FILE") or config._env_file)


def backup_dir() -> Path:
    return Path(os.environ.get("KEEPER_BACKUP_DIR") or (config._env_file.parent / "logs"))


def worker_log() -> Path:
    return Path(os.environ.get("KEEPER_WORKER_LOG") or (config._env_file.parent / "logs" / "keeper_worker.log"))


def _clean(value: bytes | str) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return value.strip().strip('"').strip("'").strip().lower()


def current(path: Path | None = None, key: str = MAIN_KEY) -> str:
    """A setting as written in .env right now ("" if the line is missing). Only used for brain lines."""
    path = Path(path or env_path())
    lines = _line_re(key).findall(path.read_bytes())
    return _clean(lines[-1][1]) if lines else ""


def current_bezel(path: Path | None = None) -> tuple[str, str]:
    """(provider, source) for BEZEL: the BRAIN_BEZEL line in .env, or the config pin if it's absent."""
    written = current(path, BEZEL_KEY)
    if written:
        return _provider(written)[0], ".env"
    return _provider(config.AGENT_BRAINS.get("BEZEL", ""))[0] or "", "config pin"


def set_main_brain(value, path: Path | None = None, backups: Path | None = None) -> dict:
    """Rewrite only the LLM_PROVIDER= line. Returns {"previous", "now", "backup"} (backup = file name)."""
    return _set_line(MAIN_KEY, value, path, backups, add_if_missing=False)


def set_bezel_brain(value, path: Path | None = None, backups: Path | None = None) -> dict:
    """Rewrite only the BRAIN_BEZEL= line (added right under LLM_PROVIDER, or at the end, if missing)."""
    return _set_line(BEZEL_KEY, value, path, backups, add_if_missing=True)


def _set_line(key: str, value, path, backups, add_if_missing: bool) -> dict:
    if not isinstance(value, str) or value.strip().lower() not in CHOICES:
        raise BrainSwitchError("Choose gemini or xai.")
    value = value.strip().lower()
    path, backups = Path(path or env_path()), Path(backups or backup_dir())
    if not path.exists():
        raise BrainSwitchError("There's no .env file to change.", 409)
    raw = path.read_bytes()
    found = list(_line_re(key).finditer(raw))
    if len(found) > 1 or (not found and not add_if_missing):
        raise BrainSwitchError(f"Expected exactly one {key}= line in .env; change it by hand.", 409)
    if found:
        m = found[0]
        previous = _clean(m.group(2))
        new = raw[:m.start(2)] + value.encode("ascii") + raw[m.end(2):]
    else:
        previous = ""
        nl = b"\r\n" if b"\r\n" in raw else b"\n"
        line = key.encode() + b"=" + value.encode("ascii")
        main = list(_line_re(MAIN_KEY).finditer(raw))
        if main:  # right under the MAIN BRAIN line
            nxt = raw.find(b"\n", main[0].start())
            end = len(raw) if nxt < 0 else nxt + 1
            new = raw[:end] + (b"" if raw[:end].endswith(b"\n") else nl) + line + nl + raw[end:]
        else:
            new = raw + (b"" if not raw or raw.endswith(nl) else nl) + line + nl
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backups / f".env.bak-{stamp}"
    n = 1
    while backup.exists():
        n += 1
        backup = backups / f".env.bak-{stamp}-{n}"
    shutil.copy2(path, backup)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(10):  # Windows: the worker may be reading .env for a moment
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"previous": previous, "now": value, "backup": backup.name}


def _provider(setting: str) -> tuple[str, str]:
    p, _, m = str(setting or "").partition(":")
    p = p.strip().lower()
    return ("xai" if p == "grok" else p), m.strip()


def resolved(values: dict) -> list[dict]:
    """Each agent's brain as the worker will resolve it from these .env values (same rules as
    llm.brain_for): [{"agent", "brain", "pinned", "note"}]. Keys are only checked for presence."""
    from . import envelope as env
    main = _provider(values.get("LLM_PROVIDER", "mock"))[0]
    models = {"xai": values.get("XAI_MODEL") or "grok-4.6", "gemini": values.get("GEMINI_MODEL") or "gemini-2.5-flash",
              "claude": values.get("CLAUDE_MODEL") or "claude-sonnet-4-5"}
    has_key = lambda p: any(str(values.get(k) or os.environ.get(k) or "").strip()  # noqa: E731
                            for k in config.PROVIDER_KEYS.get(p, ()))
    default = f"{main}:{models[main]}" if main in models else main
    out = []
    for agent in env.roster():
        setting = values.get(f"BRAIN_{agent}") or config.AGENT_BRAINS.get(agent, "")
        pinned, note, brain = bool(setting), "", default
        if main == "mock":
            brain = "mock"
        elif setting:
            p, m = _provider(setting)
            if p in models and has_key(p):
                brain = f"{p}:{m or models[p]}"
            elif p in models:
                note = f"no {p} key, using main"
            else:
                note = f"unknown '{p}', using main"
        out.append({"agent": agent, "brain": brain, "pinned": pinned, "note": note})
    return out


def worker_reported(path: Path | None = None) -> dict | None:
    """The latest `brains:` line in the Keeper worker's log: {"at", "main", "text"} or None."""
    path = Path(path or worker_log())
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 200_000))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    found = _BRAINS.findall(text)
    if not found:
        return None
    at, line = found[-1]
    main = re.match(r"main=([\w-]+)", line)
    return {"at": at, "main": main.group(1) if main else "", "text": line[:600]}


def panel_info() -> dict:
    """Everything the dashboard shows about brains (no secrets)."""
    try:
        values = config.read_env_file(env_path())
        main = current()
    except OSError:
        return {"error": "Cannot read .env right now.", "worker": worker_reported()}
    bezel, source = current_bezel()
    return {"main": main, "bezel": bezel, "bezel_source": source, "agents": resolved(values),
            "worker": worker_reported()}
