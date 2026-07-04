from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from data import matching

if TYPE_CHECKING:
    from db import Pool, Row


class AliasService:
    """Admin-curated accepted names per servant. Global. Cached in memory and
    reloaded on change, so the hot path (a guess) is an in-memory set lookup."""

    def __init__(self, pool: "Pool") -> None:
        self.pool = pool
        self._by_servant: dict[int, frozenset[str]] = {}
        # servant_id -> display forms ("as typed"), one per normalized term, for the
        # reveal's "Also accepted" line so players learn the shortcuts.
        self._display_by_servant: dict[int, tuple[str, ...]] = {}
        self._all_terms: frozenset[str] = frozenset()
        self._terms_cache: dict[frozenset[int], frozenset[str]] = {}

    async def reload(self) -> None:
        rows = await self.pool.fetch(
            "SELECT servant_id, alias, norm FROM servant_aliases ORDER BY id"
        )
        grouped: dict[int, set[str]] = defaultdict(set)
        display: dict[int, dict[str, str]] = defaultdict(dict)  # norm -> first display seen
        for r in rows:
            grouped[r["servant_id"]].add(r["norm"])
            display[r["servant_id"]].setdefault(r["norm"], r["alias"])
        self._by_servant = {sid: frozenset(s) for sid, s in grouped.items()}
        self._display_by_servant = {sid: tuple(d.values()) for sid, d in display.items()}
        self._all_terms = frozenset(t for terms in grouped.values() for t in terms)
        self._terms_cache = {}

    def for_servant(self, servant_id: int) -> frozenset[str]:
        return self._by_servant.get(servant_id, frozenset())

    def display_for(self, servant_id: int) -> tuple[str, ...]:
        """The human-typed alias forms for a servant (for the reveal)."""
        return self._display_by_servant.get(servant_id, ())

    def all_terms(self, exclude: "frozenset[int]" = frozenset()) -> frozenset[str]:
        """Every accepted alias across all servants (normalized). Used to decide
        whether a wrong message resembles a guess, so romanization variants like
        'Artoria' (Atlas: 'Altria') still get acknowledged. `exclude` drops the aliases
        of the given servant ids -- EN rounds pass the JP-only ids so a JP servant's
        alias isn't acknowledged there. Cached per exclude set (cleared on reload)."""
        if not exclude:
            return self._all_terms
        cached = self._terms_cache.get(exclude)
        if cached is None:
            cached = frozenset(
                t
                for sid, terms in self._by_servant.items()
                if sid not in exclude
                for t in terms
            )
            self._terms_cache[exclude] = cached
        return cached

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

    async def list_for(self, servant_id: int) -> "list[Row]":
        return await self.pool.fetch(
            "SELECT * FROM servant_aliases WHERE servant_id = $1 ORDER BY id", servant_id
        )
