from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db import Pool, Row


class WarService:
    """Passive faction war: mod-run seasons, 2-4 factions, +1 per member level-up. Scores only
    climb (never drop). Gated by the contract whitelist (the cog only loads when it's set)."""

    def __init__(self, pool: "Pool") -> None:
        self.pool = pool

    async def active(self, guild_id: int) -> bool:
        return bool(await self.pool.fetchval("SELECT active FROM war WHERE guild_id = $1", guild_id))

    async def start(
        self,
        guild_id: int,
        names: "list[str]",
        banner: "bytes | None" = None,
        ends_at: "int | None" = None,
        channel_id: "int | None" = None,
        name: "str | None" = None,
        description: "str | None" = None,
    ) -> None:
        """Open a season with the given faction names (2-4), resetting all scores and members.
        `banner` is an optional image shown on the war; `ends_at` (unix) auto-ends it, announced
        in `channel_id`. `name`/`description` are an optional war title + blurb shown in
        /warstatus and the announcements."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM war_factions WHERE guild_id = $1", guild_id)
                await conn.execute("DELETE FROM war_members WHERE guild_id = $1", guild_id)
                for slot, fname in enumerate(names):
                    await conn.execute(
                        "INSERT INTO war_factions (guild_id, slot, name, score) VALUES ($1, $2, $3, 0)",
                        guild_id,
                        slot,
                        fname,
                    )
                await conn.execute(
                    "INSERT INTO war "
                    "(guild_id, active, started_at, banner, ends_at, channel_id, name, description) "
                    "VALUES ($1, 1, CURRENT_TIMESTAMP, $2, $3, $4, $5, $6) "
                    "ON CONFLICT (guild_id) DO UPDATE SET active = 1, started_at = CURRENT_TIMESTAMP, "
                    "banner = $2, ends_at = $3, channel_id = $4, name = $5, description = $6",
                    guild_id,
                    banner,
                    ends_at,
                    channel_id,
                    name,
                    description,
                )

    async def banner(self, guild_id: int) -> "bytes | None":
        return await self.pool.fetchval("SELECT banner FROM war WHERE guild_id = $1", guild_id)

    async def ends_at(self, guild_id: int) -> "int | None":
        return await self.pool.fetchval("SELECT ends_at FROM war WHERE guild_id = $1", guild_id)

    async def name(self, guild_id: int) -> "str | None":
        return await self.pool.fetchval("SELECT name FROM war WHERE guild_id = $1", guild_id)

    async def description(self, guild_id: int) -> "str | None":
        return await self.pool.fetchval("SELECT description FROM war WHERE guild_id = $1", guild_id)

    async def expired(self) -> "list[Row]":
        """Active wars past their end time -- (guild_id, channel_id) for the auto-end ticker."""
        return await self.pool.fetch(
            "SELECT guild_id, channel_id FROM war WHERE active = 1 AND ends_at IS NOT NULL "
            "AND ends_at <= CAST(strftime('%s', 'now') AS INTEGER)"
        )

    async def end(self, guild_id: int) -> None:
        await self.pool.execute("UPDATE war SET active = 0 WHERE guild_id = $1", guild_id)

    async def standings(self, guild_id: int) -> "list[Row]":
        """Factions with live member counts, ranked by score (then slot)."""
        return await self.pool.fetch(
            "SELECT f.slot, f.name, f.score, "
            "  (SELECT COUNT(*) FROM war_members m "
            "   WHERE m.guild_id = f.guild_id AND m.slot = f.slot) AS members "
            "FROM war_factions f WHERE f.guild_id = $1 ORDER BY f.score DESC, f.slot",
            guild_id,
        )

    async def member(self, guild_id: int, user_id: int) -> "Row | None":
        return await self.pool.fetchrow(
            "SELECT m.slot, m.score, f.name FROM war_members m "
            "JOIN war_factions f ON f.guild_id = m.guild_id AND f.slot = m.slot "
            "WHERE m.guild_id = $1 AND m.user_id = $2",
            guild_id,
            user_id,
        )

    async def join(
        self, guild_id: int, user_id: int, choice: "str | None" = None
    ) -> "tuple[str | None, bool]":
        """Place the user on a faction, locked for the season. `choice` (a faction name) picks
        that side; otherwise auto-place on the smallest. Returns (faction_name, already_joined);
        name is None if there are no factions or `choice` matches none."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT f.name FROM war_members m JOIN war_factions f "
                    "ON f.guild_id = m.guild_id AND f.slot = m.slot "
                    "WHERE m.guild_id = $1 AND m.user_id = $2",
                    guild_id,
                    user_id,
                )
                if existing is not None:
                    return existing["name"], True
                factions = await conn.fetch(
                    "SELECT slot, name FROM war_factions WHERE guild_id = $1 ORDER BY slot", guild_id
                )
                if not factions:
                    return None, False
                if choice is not None and choice.strip():
                    target = next(
                        (f for f in factions if f["name"].lower() == choice.strip().lower()), None
                    )
                    if target is None:
                        return None, False
                else:
                    counts = {f["slot"]: 0 for f in factions}
                    for r in await conn.fetch(
                        "SELECT slot, COUNT(*) AS n FROM war_members WHERE guild_id = $1 GROUP BY slot",
                        guild_id,
                    ):
                        counts[r["slot"]] = r["n"]
                    target = min(factions, key=lambda f: counts[f["slot"]])
                await conn.execute(
                    "INSERT INTO war_members (guild_id, user_id, slot, score) VALUES ($1, $2, $3, 0)",
                    guild_id,
                    user_id,
                    target["slot"],
                )
                return target["name"], False

    async def add_points(self, guild_id: int, user_id: int, n: int) -> None:
        """Award n points to the user's faction + personal tally -- but only if a war is active
        and the user has joined one. A no-op otherwise (safe to call on every level-up)."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if not await conn.fetchval("SELECT active FROM war WHERE guild_id = $1", guild_id):
                    return
                row = await conn.fetchrow(
                    "SELECT slot FROM war_members WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
                if row is None:
                    return
                await conn.execute(
                    "UPDATE war_factions SET score = score + $3 WHERE guild_id = $1 AND slot = $2",
                    guild_id,
                    row["slot"],
                    n,
                )
                await conn.execute(
                    "UPDATE war_members SET score = score + $3 WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                    n,
                )

    async def faction_members(self, guild_id: int, slot: int) -> "list[int]":
        rows = await self.pool.fetch(
            "SELECT user_id FROM war_members WHERE guild_id = $1 AND slot = $2", guild_id, slot
        )
        return [r["user_id"] for r in rows]
