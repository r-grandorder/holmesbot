from __future__ import annotations

import asyncpg

# Session-level advisory lock electing the single Discord gateway leader. It is
# held on a dedicated connection and released when that connection closes. During
# a blue/green deploy the new task blocks on this until the old task lets go, so
# exactly one task is ever connected to the gateway.
GATEWAY_ADVISORY_LOCK_KEY = 0x42554E59414E0001  # "BUNYAN" + 0001


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: asyncpg.Pool | None = None
        self._lock_conn: asyncpg.Connection | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._lock_conn is not None:
            await self._lock_conn.close()
            self._lock_conn = None
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def try_acquire_gateway_lock(self) -> bool:
        """Try to become the gateway leader. Returns True once the lock is held."""
        if self._lock_conn is None:
            self._lock_conn = await asyncpg.connect(self._dsn)
        return bool(
            await self._lock_conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", GATEWAY_ADVISORY_LOCK_KEY
            )
        )
