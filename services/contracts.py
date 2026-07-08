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

    async def pity_count(self, guild_id: int, user_id: int) -> int:
        """Rolls toward the next guaranteed 5-star. Only the guarantee resets this; a natural
        5-star carries over (does not reset it)."""
        val = await self.pool.fetchval(
            "SELECT pity_rolls FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return val or 0

    async def set_pity(self, guild_id: int, user_id: int, count: int) -> None:
        await self.pool.execute(
            "INSERT INTO grail_balance (guild_id, user_id, pity_rolls) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET pity_rolls = $3",
            guild_id,
            user_id,
            count,
        )

    async def get_wish(self, guild_id: int, user_id: int) -> "int | None":
        """The servant id the user is chasing (its personal boosted summon odds), or None."""
        return await self.pool.fetchval(
            "SELECT wish_servant_id FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )

    async def set_wish(self, guild_id: int, user_id: int, servant_id: "int | None") -> None:
        """Set (or clear, with None) the user's wished servant."""
        await self.pool.execute(
            "INSERT INTO grail_balance (guild_id, user_id, wish_servant_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET wish_servant_id = $3",
            guild_id,
            user_id,
            servant_id,
        )

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

    async def set_active_level(self, guild_id: int, user_id: int, level: int) -> None:
        """Mod override: set the active servant's level, resetting intra-level xp to 0. The
        caller validates `level` against the grail cap first."""
        await self.pool.execute(
            "UPDATE servant_contracts SET level = $3, xp = 0, updated_at = CURRENT_TIMESTAMP "
            "WHERE guild_id = $1 AND user_id = $2 AND active = 1",
            guild_id,
            user_id,
            level,
        )

    async def summon_tickets(self, guild_id: int, user_id: int) -> int:
        return await self.pool.fetchval(
            "SELECT summon_tickets FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        ) or 0

    async def grant_tickets(self, guild_id: int, user_id: int, n: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO grail_balance (guild_id, user_id, summon_tickets) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET summon_tickets = summon_tickets + $3 "
            "RETURNING summon_tickets",
            guild_id,
            user_id,
            n,
        )

    async def set_tickets(self, guild_id: int, user_id: int, n: int) -> int:
        """Mod override: set the ticket balance to exactly n."""
        await self.pool.execute(
            "INSERT INTO grail_balance (guild_id, user_id, summon_tickets) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET summon_tickets = $3",
            guild_id,
            user_id,
            n,
        )
        return n

    async def use_ticket(self, guild_id: int, user_id: int) -> bool:
        """Consume one Summon Ticket; returns True if one was spent."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                bal = await conn.fetchval(
                    "SELECT summon_tickets FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
                if not bal:
                    return False
                await conn.execute(
                    "UPDATE grail_balance SET summon_tickets = summon_tickets - 1 "
                    "WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
                return True

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

    async def set_grails(self, guild_id: int, user_id: int, n: int) -> int:
        """Mod override: set the grail balance to exactly n."""
        await self.pool.execute(
            "INSERT INTO grail_balance (guild_id, user_id, balance) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET balance = $3",
            guild_id,
            user_id,
            n,
        )
        return n

    async def apply_grail(
        self, guild_id: int, giver_id: int, target_id: int
    ) -> "tuple[str, int | None, int | None]":
        """Spend one of giver's grails to raise target's active servant cap by GRAIL_STEP
        (giver == target for a self-grail). Allowed at any level -- it just banks another +5 of
        headroom. Returns (status, new_cap, servant_id) -- status in
        {'ok','no_contract','no_grails'}; cap is None unless 'ok', servant_id None only for
        'no_contract'."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT servant_id, grails_used FROM servant_contracts "
                    "WHERE guild_id = $1 AND user_id = $2 AND active = 1",
                    guild_id,
                    target_id,
                )
                if row is None:
                    return "no_contract", None, None
                bal = await conn.fetchval(
                    "SELECT balance FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    giver_id,
                )
                if not bal:
                    return "no_grails", None, row["servant_id"]
                await conn.execute(
                    "UPDATE grail_balance SET balance = balance - 1 "
                    "WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    giver_id,
                )
                await conn.execute(
                    "UPDATE servant_contracts SET grails_used = grails_used + 1, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE guild_id = $1 AND user_id = $2 AND servant_id = $3",
                    guild_id,
                    target_id,
                    row["servant_id"],
                )
                return "ok", contract_game.level_cap(row["grails_used"] + 1), row["servant_id"]

    async def board(self, guild_id: int) -> "list[Row]":
        """All of the guild's contracts, ranked by level (then grails). The cog applies
        the optional class filter + top-N slice in-app (the set is small)."""
        return await self.pool.fetch(
            "SELECT user_id, servant_id, level, grails_used FROM servant_contracts "
            "WHERE guild_id = $1 ORDER BY level DESC, grails_used DESC",
            guild_id,
        )

    async def duel_reward_count(self, guild_id: int, user_id: int) -> int:
        """Reward-earning duels the user has won today (drives the daily cap)."""
        val = await self.pool.fetchval(
            "SELECT rewarded FROM duel_daily "
            "WHERE guild_id = $1 AND user_id = $2 AND day = date('now')",
            guild_id,
            user_id,
        )
        return val or 0

    async def bump_duel_reward(self, guild_id: int, user_id: int) -> int:
        """Record a reward-earning duel win for today; returns the new daily count."""
        return await self.pool.fetchval(
            "INSERT INTO duel_daily (guild_id, user_id, day, rewarded) "
            "VALUES ($1, $2, date('now'), 1) "
            "ON CONFLICT (guild_id, user_id, day) DO UPDATE SET rewarded = rewarded + 1 "
            "RETURNING rewarded",
            guild_id,
            user_id,
        )
