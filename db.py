from __future__ import annotations

import asyncio
import datetime as dt
import re
import sqlite3

import aiosqlite

# The bot runs as a single process on one host (self-hosted Docker + watchtower),
# so there is no gateway leader to elect: a single SQLite connection guarded by
# one lock fully serializes DB access, which is correct here and sidesteps SQLite
# writer contention entirely.

# asyncpg used "$1"-style placeholders; SQLite uses "?1" indexed parameters,
# which (like Postgres) let one bound value be referenced repeatedly and out of
# order. So "$3" -> "?3" is a faithful, mechanical translation.
_PARAM = re.compile(r"\$(\d+)")


def _to_sqlite(sql: str) -> str:
    return _PARAM.sub(r"?\1", sql)


def _command_tag(sql: str, rowcount: int) -> str:
    """Mimic asyncpg's command tag (e.g. 'DELETE 1') so callers comparing the
    return value of execute() keep working."""
    op = sql.lstrip().split(None, 1)[0].upper()
    if op == "INSERT":
        return f"INSERT 0 {rowcount}"
    return f"{op} {rowcount}"


def _adapt_datetime(value: dt.datetime) -> str:
    """Store datetimes as 'YYYY-MM-DD HH:MM:SS' in UTC, matching SQLite's own
    CURRENT_TIMESTAMP / datetime('now') so text comparisons stay chronological."""
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S")


sqlite3.register_adapter(dt.datetime, _adapt_datetime)


def _db_path(url: str) -> str:
    """Accept a dbmate-style sqlite URL ('sqlite:./x', 'sqlite3:///x') or a bare
    path and return the filesystem path the driver opens."""
    for prefix in ("sqlite3://", "sqlite://", "sqlite3:", "sqlite:"):
        if url.startswith(prefix):
            url = url[len(prefix) :]
            break
    return url.split("?", 1)[0] or ":memory:"


class Connection:
    """asyncpg-Connection-like wrapper over one aiosqlite connection. Used while a
    transaction holds the pool lock, so it does not take the lock itself."""

    def __init__(self, raw: aiosqlite.Connection) -> None:
        self._raw = raw

    async def execute(self, sql: str, *args: object) -> str:
        async with self._raw.execute(_to_sqlite(sql), args) as cur:
            return _command_tag(sql, cur.rowcount)

    async def fetch(self, sql: str, *args: object) -> list[aiosqlite.Row]:
        async with self._raw.execute(_to_sqlite(sql), args) as cur:
            return list(await cur.fetchall())

    async def fetchrow(self, sql: str, *args: object) -> aiosqlite.Row | None:
        async with self._raw.execute(_to_sqlite(sql), args) as cur:
            return await cur.fetchone()

    async def fetchval(self, sql: str, *args: object) -> object:
        row = await self.fetchrow(sql, *args)
        return row[0] if row is not None else None

    def transaction(self) -> "_Transaction":
        return _Transaction(self._raw)


class _Transaction:
    def __init__(self, raw: aiosqlite.Connection) -> None:
        self._raw = raw

    async def __aenter__(self) -> "_Transaction":
        await self._raw.execute("BEGIN")
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is None:
            await self._raw.execute("COMMIT")
        else:
            await self._raw.execute("ROLLBACK")
        return False


class _Acquire:
    def __init__(self, raw: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self._raw = raw
        self._lock = lock

    async def __aenter__(self) -> Connection:
        await self._lock.acquire()
        return Connection(self._raw)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self._lock.release()
        return False


class Pool:
    """asyncpg-Pool-like facade over a single serialized aiosqlite connection."""

    def __init__(self, raw: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self._lock = lock
        self._conn = Connection(raw)
        self._raw = raw

    async def execute(self, sql: str, *args: object) -> str:
        async with self._lock:
            return await self._conn.execute(sql, *args)

    async def fetch(self, sql: str, *args: object) -> list[aiosqlite.Row]:
        async with self._lock:
            return await self._conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: object) -> aiosqlite.Row | None:
        async with self._lock:
            return await self._conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: object) -> object:
        async with self._lock:
            return await self._conn.fetchval(sql, *args)

    def acquire(self) -> _Acquire:
        return _Acquire(self._raw, self._lock)


Row = aiosqlite.Row


class Database:
    def __init__(self, url: str) -> None:
        self._path = _db_path(url)
        self._conn: aiosqlite.Connection | None = None
        self.pool: Pool | None = None

    async def connect(self) -> None:
        # autocommit (isolation_level=None): standalone statements persist
        # immediately; transactions are managed explicitly via BEGIN/COMMIT.
        conn = await aiosqlite.connect(self._path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA synchronous=NORMAL")
        self._conn = conn
        self.pool = Pool(conn, asyncio.Lock())

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self.pool = None
