from __future__ import annotations

from collections import defaultdict
from typing import Callable

import asyncpg


class RestrictionService:
    """Content-policy restrictions on which servant art may appear.

    Ships empty. `build_allow()` returns the predicate the servant index uses to
    gate the eligible pool; the same rules also govern reveal art in the cogs.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def build_allow(self) -> Callable[[int, str], bool]:
        rules = await self.pool.fetch(
            "SELECT servant_id, scope, ascension_keys FROM restricted_servants"
        )
        full: set[int] = set()
        per_ascension: dict[int, set[str]] = defaultdict(set)
        for r in rules:
            if r["scope"] == "full":
                full.add(r["servant_id"])
            else:
                per_ascension[r["servant_id"]].update(r["ascension_keys"])

        def allow(servant_id: int, ascension_key: str) -> bool:
            if servant_id in full:
                return False
            return ascension_key not in per_ascension.get(servant_id, ())

        return allow

    async def add(
        self,
        servant_id: int,
        scope: str,
        ascension_keys: list[str],
        reason: str | None,
        added_by: int,
    ) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO restricted_servants (servant_id, scope, ascension_keys, reason, added_by)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            """,
            servant_id,
            scope,
            ascension_keys,
            reason,
            added_by,
        )

    async def remove(self, restriction_id: int) -> bool:
        res = await self.pool.execute(
            "DELETE FROM restricted_servants WHERE id = $1", restriction_id
        )
        return res == "DELETE 1"

    async def list_all(self) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM restricted_servants ORDER BY servant_id, id"
        )
