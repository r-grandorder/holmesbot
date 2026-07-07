from __future__ import annotations

from typing import TYPE_CHECKING

from data import contract_game

if TYPE_CHECKING:
    from db import Pool, Row


class ContractService:
    """Per-user contracted servant + grail progression. QP itself lives in ScoringService;
    this only owns servant_contracts + grail_balance."""

    def __init__(self, pool: "Pool") -> None:
        self.pool = pool

    async def active(self, guild_id: int, user_id: int) -> "Row | None":
        return await self.pool.fetchrow(
            "SELECT * FROM servant_contracts WHERE guild_id = $1 AND user_id = $2 AND active = 1",
            guild_id,
            user_id,
        )

    async def has_contract(self, guild_id: int, user_id: int, servant_id: int) -> bool:
        """Whether the user has ever contracted this servant (a progress row exists)."""
        val = await self.pool.fetchval(
            "SELECT 1 FROM servant_contracts WHERE guild_id = $1 AND user_id = $2 AND servant_id = $3",
            guild_id,
            user_id,
            servant_id,
        )
        return val is not None

    async def contract(self, guild_id: int, user_id: int, servant_id: int) -> None:
        """Make `servant_id` the user's active contract, atomically: deactivate any current
        contract, then activate this one -- creating it at level 1 if new, else resuming its
        saved progress."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE servant_contracts SET active = 0, updated_at = CURRENT_TIMESTAMP "
                    "WHERE guild_id = $1 AND user_id = $2 AND active = 1",
                    guild_id,
                    user_id,
                )
                await conn.execute(
                    "INSERT INTO servant_contracts (guild_id, user_id, servant_id, active) "
                    "VALUES ($1, $2, $3, 1) "
                    "ON CONFLICT (guild_id, user_id, servant_id) "
                    "DO UPDATE SET active = 1, updated_at = CURRENT_TIMESTAMP",
                    guild_id,
                    user_id,
                    servant_id,
                )

    async def add_xp(
        self, guild_id: int, user_id: int, amount: int
    ) -> "tuple[int, int, int, int] | None":
        """Add xp to the active contract and roll it into levels. Returns
        (servant_id, old_level, new_level, cap) or None if there's no active contract."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT servant_id, level, xp, grails_used FROM servant_contracts "
                    "WHERE guild_id = $1 AND user_id = $2 AND active = 1",
                    guild_id,
                    user_id,
                )
                if row is None:
                    return None
                cap = contract_game.level_cap(row["grails_used"])
                new_level, new_xp = contract_game.apply_xp(
                    row["level"], row["xp"] + amount, cap
                )
                await conn.execute(
                    "UPDATE servant_contracts SET level = $4, xp = $5, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE guild_id = $1 AND user_id = $2 AND servant_id = $3",
                    guild_id,
                    user_id,
                    row["servant_id"],
                    new_level,
                    new_xp,
                )
                return row["servant_id"], row["level"], new_level, cap

    async def grail_balance(self, guild_id: int, user_id: int) -> int:
        val = await self.pool.fetchval(
            "SELECT balance FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return val or 0

    async def grant_grails(self, guild_id: int, user_id: int, n: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO grail_balance (guild_id, user_id, balance) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET balance = balance + $3 "
            "RETURNING balance",
            guild_id,
            user_id,
            n,
        )

    async def apply_grail(self, guild_id: int, user_id: int) -> "tuple[str, int | None]":
        """Spend one grail to raise the active servant's cap by GRAIL_STEP. Returns a
        (status, cap) pair -- status in {'ok','no_contract','not_max','no_grails'}."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT servant_id, level, grails_used FROM servant_contracts "
                    "WHERE guild_id = $1 AND user_id = $2 AND active = 1",
                    guild_id,
                    user_id,
                )
                if row is None:
                    return "no_contract", None
                cap = contract_game.level_cap(row["grails_used"])
                if row["level"] < cap:
                    return "not_max", cap
                bal = await conn.fetchval(
                    "SELECT balance FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
                if not bal:
                    return "no_grails", cap
                await conn.execute(
                    "UPDATE grail_balance SET balance = balance - 1 "
                    "WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
                await conn.execute(
                    "UPDATE servant_contracts SET grails_used = grails_used + 1, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE guild_id = $1 AND user_id = $2 AND servant_id = $3",
                    guild_id,
                    user_id,
                    row["servant_id"],
                )
                return "ok", cap + contract_game.GRAIL_STEP

    async def board(self, guild_id: int) -> "list[Row]":
        """All of the guild's contracts, ranked by level (then grails). The cog applies
        the optional class filter + top-N slice in-app (the set is small)."""
        return await self.pool.fetch(
            "SELECT user_id, servant_id, level, grails_used FROM servant_contracts "
            "WHERE guild_id = $1 ORDER BY level DESC, grails_used DESC",
            guild_id,
        )
