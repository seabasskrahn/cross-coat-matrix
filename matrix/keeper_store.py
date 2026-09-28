"""Keeper storage: create / read / update job rows in the `jobs` table (docs/KEEPER_SPEC.md).

Used by the main Matrix flow (runner.py / graph.py) and by the Keeper worker.

- PgStore: Postgres via CHECKPOINT_DB_URL. Finds `public.jobs` (preferred) or the older `"Jobs"`.
  Autocommit: every append/update is saved the moment it happens, so a crash loses nothing.
- MemoryStore: same methods, kept in memory. Used when CHECKPOINT_DB_URL is empty (tests,
  offline runs), or if Postgres can't be reached (a warning is logged).

Locks: a job being worked on holds a Postgres advisory lock (LOCK_NAMESPACE, job id) on the
store's connection. The worker only claims rows it can lock, so it never works on a job the
main flow is in the middle of (and vice versa). Locks vanish if the process dies.
"""
from __future__ import annotations

import copy
import logging
import threading

from . import config
from .envelope import PICKED_UP, as_list, entry, is_pending

TABLE_CANDIDATES = ("jobs", "Jobs")  # lowercase wins if both exist
LOCK_NAMESPACE = 4242                # first key of pg_advisory_lock(ns, job_id)
COLUMNS = ("message", "tasks", "agents", "drafts", "approvals", "step_log")
ARRAY_COLUMNS = COLUMNS[1:]

log = logging.getLogger("keeper_store")


def _job_id(job) -> int | None:
    return job.get("id") if isinstance(job, dict) else job


def _sync_append(job, column, item):
    if isinstance(job, dict):
        job[column] = as_list(job.get(column)) + [item]


class MemoryStore:
    """In-memory jobs table with the same interface as PgStore."""

    table_name = "memory"

    def __init__(self, rows=None):
        self.rows: dict[int, dict] = {r["id"]: copy.deepcopy(r) for r in rows or []}
        self.locked: set[int] = set()
        self.claims: list[int] = []

    def _new_id(self) -> int:
        return max(self.rows, default=0) + 1

    def create(self, message: str, lock: bool = False, **fields) -> dict:
        row = {"id": self._new_id(), "message": message,
               **{c: copy.deepcopy(fields.get(c, [])) for c in ARRAY_COLUMNS}}
        self.rows[row["id"]] = row
        if lock:
            self.locked.add(row["id"])
        return copy.deepcopy(row)

    def get(self, job_id: int) -> dict | None:
        row = self.rows.get(job_id)
        return copy.deepcopy(row) if row else None

    def candidates(self) -> list[dict]:
        return [copy.deepcopy(r) for _, r in sorted(self.rows.items())]

    def claim(self, job_id: int) -> dict | None:
        row = self.rows.get(job_id)
        if row is None or job_id in self.locked or not is_pending(row):
            return None
        self.locked.add(job_id)
        self.claims.append(job_id)
        job = copy.deepcopy(row)
        self.append(job, "step_log", entry(PICKED_UP))
        return job

    def append(self, job, column: str, item: dict) -> None:
        assert column in ARRAY_COLUMNS
        row = self.rows.get(_job_id(job))
        if row is not None:
            row[column] = as_list(row.get(column)) + [copy.deepcopy(item)]
        _sync_append(job, column, item)

    def set_field(self, job, column: str, value) -> None:
        assert column in COLUMNS
        row = self.rows.get(_job_id(job))
        if row is not None:
            row[column] = copy.deepcopy(value)
        if isinstance(job, dict):
            job[column] = value

    def update_item(self, job, column: str, index: int, changes: dict) -> None:
        """Merge `changes` into item `index` of an array column (e.g. answer an approval)."""
        assert column in ARRAY_COLUMNS
        row = self.rows.get(_job_id(job))
        if row is not None:
            row[column][index].update(copy.deepcopy(changes))
        if isinstance(job, dict) and job is not row:
            job[column][index].update(changes)

    def lock(self, job_id: int) -> None:
        self.locked.add(job_id)

    def release(self, job_id: int) -> None:
        self.locked.discard(job_id)

    def close(self) -> None:
        pass


class PgStore:
    """Postgres jobs table. Thread-safe (one connection, guarded by a lock)."""

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self._mutex = threading.RLock()
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self.table_name = self.detect_table()
        log.info("keeper store using table public.%s", self.table_name)

    # --- helpers ---
    def detect_table(self) -> str:
        rows = self.conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relname = ANY(%s)",
            [list(TABLE_CANDIDATES)]).fetchall()
        found = {r["relname"] for r in rows}
        for name in TABLE_CANDIDATES:
            if name in found:
                return name
        raise RuntimeError('no jobs table found (looked for public.jobs and public."Jobs")')

    @property
    def table(self):
        from psycopg import sql
        return sql.Identifier("public", self.table_name)

    def _exec(self, build, params=()):
        """Run a query built by build(table_identifier). If the table was renamed, find it again once."""
        import psycopg
        with self._mutex:
            try:
                return self.conn.execute(build(self.table), params)
            except psycopg.errors.UndefinedTable:
                old, self.table_name = self.table_name, self.detect_table()
                log.warning("table public.%s is gone; now using public.%s", old, self.table_name)
                return self.conn.execute(build(self.table), params)

    @staticmethod
    def _cols():
        from psycopg import sql
        return sql.SQL(", ").join(sql.Identifier(c) for c in ("id",) + COLUMNS)

    # --- create / read ---
    def create(self, message: str, lock: bool = False, **fields) -> dict:
        """Insert a new job. With lock=True the row is locked for this process before anyone else
        can see it (insert + advisory lock in one transaction)."""
        from psycopg import sql
        from psycopg.types.json import Jsonb
        values = [message] + [Jsonb(fields.get(c, [])) for c in ARRAY_COLUMNS]
        q = lambda t: sql.SQL("INSERT INTO {} ({}) VALUES (%s, %s, %s, %s, %s, %s) RETURNING {}").format(  # noqa: E731
            t, sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS), self._cols())
        with self._mutex, self.conn.transaction():
            row = self._exec(q, values).fetchone()
            if lock:
                self.conn.execute("SELECT pg_advisory_lock(%s, %s)", [LOCK_NAMESPACE, row["id"]])
        return row

    def get(self, job_id: int) -> dict | None:
        from psycopg import sql
        return self._exec(lambda t: sql.SQL("SELECT {} FROM {} WHERE id = %s").format(self._cols(), t),
                          [job_id]).fetchone()

    def candidates(self) -> list[dict]:
        from psycopg import sql
        return self._exec(lambda t: sql.SQL("SELECT id, approvals, step_log FROM {} ORDER BY id").format(t)
                          ).fetchall()

    def claim(self, job_id: int) -> dict | None:
        """Row lock (SKIP LOCKED) + advisory lock, re-check it's pending, log `picked up`."""
        from psycopg import sql
        q = lambda t: sql.SQL("SELECT {} FROM {} WHERE id = %s FOR UPDATE SKIP LOCKED").format(self._cols(), t)  # noqa: E731
        with self._mutex, self.conn.transaction():
            row = self._exec(q, [job_id]).fetchone()
            if row is None:
                return None
            got = self.conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok",
                                    [LOCK_NAMESPACE, job_id]).fetchone()["ok"]
            if not got:
                return None
            if not is_pending(row):
                self.release(job_id)
                return None
            self.append(row, "step_log", entry(PICKED_UP))
        return row

    # --- update (each call is saved immediately) ---
    def append(self, job, column: str, item: dict) -> None:
        from psycopg import sql
        from psycopg.types.json import Jsonb
        assert column in ARRAY_COLUMNS
        col = sql.Identifier(column)
        self._exec(lambda t: sql.SQL("UPDATE {} SET {c} = COALESCE({c}, '[]'::jsonb) || %s WHERE id = %s")
                   .format(t, c=col), [Jsonb([item]), _job_id(job)])
        _sync_append(job, column, item)

    def set_field(self, job, column: str, value) -> None:
        from psycopg import sql
        from psycopg.types.json import Jsonb
        assert column in COLUMNS
        v = value if column == "message" else Jsonb(value)
        self._exec(lambda t: sql.SQL("UPDATE {} SET {} = %s WHERE id = %s").format(t, sql.Identifier(column)),
                   [v, _job_id(job)])
        if isinstance(job, dict):
            job[column] = value

    def update_item(self, job, column: str, index: int, changes: dict) -> None:
        from psycopg import sql
        from psycopg.types.json import Jsonb
        assert column in ARRAY_COLUMNS
        col = sql.Identifier(column)
        self._exec(lambda t: sql.SQL("UPDATE {} SET {c} = jsonb_set({c}, ARRAY[%s::text], ({c} -> %s) || %s) "
                                     "WHERE id = %s").format(t, c=col),
                   [str(index), index, Jsonb(changes), _job_id(job)])
        if isinstance(job, dict):
            job[column][index].update(changes)

    # --- locks ---
    def lock(self, job_id: int) -> None:
        with self._mutex:
            self.conn.execute("SELECT pg_advisory_lock(%s, %s)", [LOCK_NAMESPACE, job_id])

    def release(self, job_id: int) -> None:
        with self._mutex:
            self.conn.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, job_id])

    def close(self) -> None:
        self.conn.close()


_store = None
_store_mutex = threading.Lock()


def get_store():
    """The shared store for this process: Postgres if CHECKPOINT_DB_URL is set, else memory."""
    global _store
    with _store_mutex:
        if _store is None:
            if config.CHECKPOINT_DB_URL:
                try:
                    _store = PgStore(config.CHECKPOINT_DB_URL)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Keeper Postgres unavailable (%s); keeping jobs in memory", type(exc).__name__)
                    _store = MemoryStore()
            else:
                _store = MemoryStore()
        return _store


def set_store(store) -> None:
    """Swap the shared store (tests)."""
    global _store
    with _store_mutex:
        _store = store
