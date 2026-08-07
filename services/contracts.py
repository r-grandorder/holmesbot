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

    async def add_xp_to(
        self, guild_id: int, user_id: int, servant_id: int, amount: int
    ) -> "tuple[int, int, int] | None":
        """Add xp to a specific owned servant (not just the active one) and roll it into levels --
        used by /ember. Returns (old_level, new_level, cap), or None if the servant isn't
        contracted. If it's already at cap, returns (level, level, cap) WITHOUT writing, so the
        caller can refuse rather than waste the item (apply_xp discards xp past the cap)."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT level, xp, grails_used FROM servant_contracts "
                    "WHERE guild_id = $1 AND user_id = $2 AND servant_id = $3",
                    guild_id,
                    user_id,
                    servant_id,
                )
                if row is None:
                    return None
                cap = contract_game.level_cap(row["grails_used"])
                if row["level"] >= cap:
                    return row["level"], row["level"], cap
                new_level, new_xp = contract_game.apply_xp(row["level"], row["xp"] + amount, cap)
                await conn.execute(
                    "UPDATE servant_contracts SET level = $4, xp = $5, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE guild_id = $1 AND user_id = $2 AND servant_id = $3",
                    guild_id,
                    user_id,
                    servant_id,
                    new_level,
                    new_xp,
                )
                return row["level"], new_level, cap

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

    async def xp_items(self, guild_id: int, user_id: int) -> "tuple[int, int]":
        """(embers, hellfire) the user holds -- the XP-feeding items spent via /ember."""
        row = await self.pool.fetchrow(
            "SELECT embers, hellfire FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return (row["embers"], row["hellfire"]) if row else (0, 0)

    async def grant_xp_item(self, guild_id: int, user_id: int, kind: str, n: int) -> int:
        """Adjust an XP-item balance (kind 'embers'|'hellfire') by n (grant +, spend -); floors at
        0. Returns the new balance."""
        col = {"embers": "embers", "hellfire": "hellfire"}[kind]  # whitelist the column name
        return await self.pool.fetchval(
            f"INSERT INTO grail_balance (guild_id, user_id, {col}) VALUES ($1, $2, MAX(0, $3)) "
            f"ON CONFLICT (guild_id, user_id) DO UPDATE SET {col} = MAX(0, {col} + $3) "
            f"RETURNING {col}",
            guild_id,
            user_id,
            n,
        )

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
        """Each player's ACTIVE contract, ranked by level (then grails) -- one row per user, so
        the leaderboard reflects the servant they're currently fielding, not their all-time
        best. The cog applies the optional class filter + top-N slice in-app."""
        return await self.pool.fetch(
            "SELECT user_id, servant_id, level, grails_used FROM servant_contracts "
            "WHERE guild_id = $1 AND active = 1 ORDER BY level DESC, grails_used DESC",
            guild_id,
        )

    async def owned(self, guild_id: int, user_id: int) -> "list[Row]":
        """Every servant this user has contracted (their roster), active first then by level.
        Drives /switch (re-activating an owned servant), its autocomplete, and /servants."""
        return await self.pool.fetch(
            "SELECT servant_id, level, grails_used, active, created_at FROM servant_contracts "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY active DESC, level DESC",
            guild_id,
            user_id,
        )

    async def battle_team(self, guild_id: int, user_id: int) -> "list[int]":
        """The user's autobattle team as servant ids in slot order (front to back). Empty if unset."""
        rows = await self.pool.fetch(
            "SELECT servant_id FROM autobattle_team WHERE guild_id = $1 AND user_id = $2 "
            "ORDER BY slot",
            guild_id,
            user_id,
        )
        return [r["servant_id"] for r in rows]

    async def set_battle_team(self, guild_id: int, user_id: int, servant_ids: "list[int]") -> None:
        """Replace the user's autobattle team with these 1-3 servant ids (front-to-back)."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM autobattle_team WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
                for slot, sid in enumerate(servant_ids[:3]):
                    await conn.execute(
                        "INSERT INTO autobattle_team (guild_id, user_id, slot, servant_id) "
                        "VALUES ($1, $2, $3, $4)",
                        guild_id,
                        user_id,
                        slot,
                        sid,
                    )

    # --- autobattle item inventory + equipment (the QP sink) ------------------------------------

    async def inventory(self, guild_id: int, user_id: int) -> "list[Row]":
        """Owned autobattle items (item_id, quantity), quantity > 0."""
        return await self.pool.fetch(
            "SELECT item_id, quantity FROM autobattle_inventory "
            "WHERE guild_id = $1 AND user_id = $2 AND quantity > 0 ORDER BY item_id",
            guild_id,
            user_id,
        )

    async def item_qty(self, guild_id: int, user_id: int, item_id: str) -> int:
        val = await self.pool.fetchval(
            "SELECT quantity FROM autobattle_inventory "
            "WHERE guild_id = $1 AND user_id = $2 AND item_id = $3",
            guild_id,
            user_id,
            item_id,
        )
        return val or 0

    async def add_item(self, guild_id: int, user_id: int, item_id: str, delta: int) -> int:
        """Adjust an item's quantity by `delta` (buy = +1, consume = -1). Floors at 0 and deletes
        the row when it hits 0. Returns the new quantity."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                cur = await conn.fetchval(
                    "SELECT quantity FROM autobattle_inventory "
                    "WHERE guild_id = $1 AND user_id = $2 AND item_id = $3",
                    guild_id,
                    user_id,
                    item_id,
                ) or 0
                new_q = max(0, cur + delta)
                if new_q == 0:
                    await conn.execute(
                        "DELETE FROM autobattle_inventory "
                        "WHERE guild_id = $1 AND user_id = $2 AND item_id = $3",
                        guild_id,
                        user_id,
                        item_id,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO autobattle_inventory (guild_id, user_id, item_id, quantity) "
                        "VALUES ($1, $2, $3, $4) "
                        "ON CONFLICT (guild_id, user_id, item_id) DO UPDATE SET quantity = $4",
                        guild_id,
                        user_id,
                        item_id,
                        new_q,
                    )
                return new_q

    async def equips(self, guild_id: int, user_id: int) -> "dict[int, str]":
        """{servant_id: item_id} for every servant the user has an item equipped on."""
        rows = await self.pool.fetch(
            "SELECT servant_id, item_id FROM autobattle_equip WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return {r["servant_id"]: r["item_id"] for r in rows}

    async def equip(self, guild_id: int, user_id: int, servant_id: int, item_id: str) -> None:
        await self.pool.execute(
            "INSERT INTO autobattle_equip (guild_id, user_id, servant_id, item_id) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, user_id, servant_id) DO UPDATE SET item_id = $4",
            guild_id,
            user_id,
            servant_id,
            item_id,
        )

    async def unequip(self, guild_id: int, user_id: int, servant_id: int) -> None:
        await self.pool.execute(
            "DELETE FROM autobattle_equip "
            "WHERE guild_id = $1 AND user_id = $2 AND servant_id = $3",
            guild_id,
            user_id,
            servant_id,
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

    async def ab_reward_count(self, guild_id: int, user_id: int, kind: str) -> int:
        """Reward-earning autobattle wins today for a mode ('pve'|'pvp'); drives that mode's cap."""
        val = await self.pool.fetchval(
            "SELECT count FROM autobattle_daily "
            "WHERE guild_id = $1 AND user_id = $2 AND day = date('now') AND kind = $3",
            guild_id,
            user_id,
            kind,
        )
        return val or 0

    async def bump_ab_reward(self, guild_id: int, user_id: int, kind: str) -> int:
        """Record a reward-earning autobattle win today for a mode; returns the new daily count."""
        return await self.pool.fetchval(
            "INSERT INTO autobattle_daily (guild_id, user_id, day, kind, count) "
            "VALUES ($1, $2, date('now'), $3, 1) "
            "ON CONFLICT (guild_id, user_id, day, kind) DO UPDATE SET count = count + 1 "
            "RETURNING count",
            guild_id,
            user_id,
            kind,
        )
