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
                # Archive an un-ended prior war (started, then replaced by a fresh /warstart)
                # before wiping it, so /warhistory keeps it. Already-ended wars were archived by
                # end(); the started_at check avoids a duplicate.
                prev = await conn.fetchrow(
                    "SELECT started_at FROM war WHERE guild_id = $1", guild_id
                )
                if prev is not None and not await self._already_archived(
                    conn, guild_id, prev["started_at"]
                ):
                    await self._archive(conn, guild_id)
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

    async def end(self, guild_id: int) -> bool:
        """Conclude the active war: snapshot it into history, mark it inactive. Returns True only
        for the call that actually ended it (active 1 -> 0). The check + flip share one
        transaction, so racing callers (/warend + the auto-end ticker) can't both reward."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if not await conn.fetchval("SELECT active FROM war WHERE guild_id = $1", guild_id):
                    return False
                await self._archive(conn, guild_id)
                await conn.execute("UPDATE war SET active = 0 WHERE guild_id = $1", guild_id)
                return True

    async def _archive(self, conn, guild_id: int) -> None:
        """Snapshot the guild's current war (factions + members + final scores) into the history
        tables. No-op with no war row or no factions. Winner = top-scoring faction, or None if
        nobody scored. Runs inside the caller's transaction."""
        war = await conn.fetchrow(
            "SELECT name, description, started_at FROM war WHERE guild_id = $1", guild_id
        )
        if war is None:
            return
        factions = await conn.fetch(
            "SELECT slot, name, score FROM war_factions WHERE guild_id = $1 ORDER BY slot", guild_id
        )
        if not factions:
            return
        top_score = max(f["score"] for f in factions)
        leaders = [f for f in factions if f["score"] == top_score]
        # Only a UNIQUE top scorer wins; a tie (or nobody scoring) leaves it null, matching
        # _end_war's "tie -> no payout" so /warhistory doesn't crown a tied faction.
        winner_slot = leaders[0]["slot"] if (top_score > 0 and len(leaders) == 1) else None
        war_id = await conn.fetchval(
            "INSERT INTO war_history (guild_id, name, description, started_at, winner_slot) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            guild_id, war["name"], war["description"], war["started_at"], winner_slot,
        )
        for f in factions:
            await conn.execute(
                "INSERT INTO war_history_factions (war_id, slot, name, score) "
                "VALUES ($1, $2, $3, $4)",
                war_id, f["slot"], f["name"], f["score"],
            )
        for m in await conn.fetch(
            "SELECT user_id, slot, score FROM war_members WHERE guild_id = $1", guild_id
        ):
            await conn.execute(
                "INSERT INTO war_history_members (war_id, user_id, slot, score) "
                "VALUES ($1, $2, $3, $4)",
                war_id, m["user_id"], m["slot"], m["score"],
            )

    async def _already_archived(self, conn, guild_id: int, started_at) -> bool:
        """Whether a war with this guild_id + started_at is already in history (dedup key)."""
        if not started_at:
            return False
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM war_history WHERE guild_id = $1 AND started_at = $2 LIMIT 1",
                guild_id,
                started_at,
            )
        )

    async def roster(self, guild_id: int) -> "list[Row]":
        """Every current-war member with their faction slot + personal score (for /warteams)."""
        return await self.pool.fetch(
            "SELECT user_id, slot, score FROM war_members WHERE guild_id = $1 "
            "ORDER BY slot, score DESC",
            guild_id,
        )

    async def history(self, guild_id: int, limit: int = 25) -> "list[Row]":
        """Past wars for the guild, newest first."""
        return await self.pool.fetch(
            "SELECT id, name, description, started_at, ended_at, winner_slot "
            "FROM war_history WHERE guild_id = $1 ORDER BY id DESC LIMIT $2",
            guild_id,
            limit,
        )

    async def history_factions(self, war_id: int) -> "list[Row]":
        return await self.pool.fetch(
            "SELECT slot, name, score FROM war_history_factions WHERE war_id = $1 ORDER BY slot",
            war_id,
        )

    async def history_members(self, war_id: int) -> "list[Row]":
        return await self.pool.fetch(
            "SELECT user_id, slot, score FROM war_history_members WHERE war_id = $1 "
            "ORDER BY slot, score DESC",
            war_id,
        )

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

    async def join(self, guild_id: int, user_id: int) -> "tuple[str | None, bool]":
        """Auto-place the user on the WEAKEST faction by points (score), ties broken by smallest
        roster then slot, locked for the season. Players can't pick a side -- this keeps new joiners
        reinforcing the underdog instead of snowballing the leader. Returns (faction_name,
        already_joined); name is None only if the war has no factions."""
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
                    "SELECT slot, name, score FROM war_factions WHERE guild_id = $1 ORDER BY slot",
                    guild_id,
                )
                if not factions:
                    return None, False
                counts = {f["slot"]: 0 for f in factions}
                for r in await conn.fetch(
                    "SELECT slot, COUNT(*) AS n FROM war_members WHERE guild_id = $1 GROUP BY slot",
                    guild_id,
                ):
                    counts[r["slot"]] = r["n"]
                # Weakest faction by points. When scores tie (e.g. war start, all 0) fall back to
                # the smallest roster, then slot, so early joiners spread out instead of stacking.
                target = min(factions, key=lambda f: (f["score"], counts[f["slot"]], f["slot"]))
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
