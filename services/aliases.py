from __future__ import annotations

from collections import defaultdict

import asyncpg

from data import matching


class AliasService:
    """Admin-curated accepted names per servant. Global. Cached in memory and
    reloaded on change, so the hot path (a guess) is an in-memory set lookup."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._by_servant: dict[int, frozenset[str]] = {}
        self._all_terms: frozenset[str] = frozenset()

    async def reload(self) -> None:
        rows = await self.pool.fetch("SELECT servant_id, norm FROM servant_aliases")
        grouped: dict[int, set[str]] = defaultdict(set)
        for r in rows:
            grouped[r["servant_id"]].add(r["norm"])
        self._by_servant = {sid: frozenset(s) for sid, s in grouped.items()}
        self._all_terms = frozenset(t for terms in grouped.values() for t in terms)

    def for_servant(self, servant_id: int) -> frozenset[str]:
        return self._by_servant.get(servant_id, frozenset())

    def all_terms(self) -> frozenset[str]:
        """Every accepted alias across all servants (normalized). Used to decide
        whether a wrong message resembles a guess, so romanization variants like
        'Artoria' (Atlas: 'Altria') still get acknowledged."""
        return self._all_terms

    async def add(self, servant_id: int, alias: str, added_by: int) -> bool:
        norm = matching.normalize(alias)
        if not norm:
            return False
        await self.pool.execute(
            "INSERT INTO servant_aliases (servant_id, alias, norm, added_by) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (servant_id, norm) DO NOTHING",
            servant_id,
            alias.strip(),
            norm,
            added_by,
        )
        await self.reload()
        return True

    async def remove(self, alias_id: int) -> bool:
        res = await self.pool.execute("DELETE FROM servant_aliases WHERE id = $1", alias_id)
        await self.reload()
        return res == "DELETE 1"

    async def list_for(self, servant_id: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM servant_aliases WHERE servant_id = $1 ORDER BY id", servant_id
        )
