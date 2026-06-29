from __future__ import annotations

from typing import TYPE_CHECKING

from branding import MAX_QP

if TYPE_CHECKING:
    from db import Pool, Row


class ScoringService:
    """QP economy. `points` = lifetime QP earned (leaderboard, monotonic).
    `balance` = spendable QP (capped at MAX_QP, moved by /pay). A win raises both."""

    def __init__(self, pool: "Pool") -> None:
        self.pool = pool

    async def award(self, guild_id: int, user_id: int, amount: int) -> int:
        """Award QP for a win; returns the new (capped) balance."""
        return await self.pool.fetchval(
            """
            INSERT INTO scores (guild_id, user_id, points, balance, wins, games)
            VALUES ($1, $2, $3, MIN($3, $4), 1, 1)
            ON CONFLICT (guild_id, user_id) DO UPDATE
            SET points = points + $3,
                balance = MIN(balance + $3, $4),
                wins = wins + 1,
                games = games + 1,
                updated_at = CURRENT_TIMESTAMP
            RETURNING balance
            """,
            guild_id,
            user_id,
            amount,
            MAX_QP,
        )

    async def get_balance(self, guild_id: int, user_id: int) -> int:
        val = await self.pool.fetchval(
            "SELECT balance FROM scores WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        return val or 0

    async def get_earned(self, guild_id: int, user_id: int) -> int:
        val = await self.pool.fetchval(
            "SELECT points FROM scores WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        return val or 0

    async def leaderboard(self, guild_id: int, limit: int = 10) -> "list[Row]":
        return await self.pool.fetch(
            "SELECT user_id, points, wins FROM scores "
            "WHERE guild_id = $1 AND points > 0 ORDER BY points DESC LIMIT $2",
            guild_id,
            limit,
        )

    async def transfer(
        self, guild_id: int, sender_id: int, receiver_id: int, amount: int
    ) -> str:
        """Move QP between players. Returns 'ok', 'insufficient', or 'overflow'."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                recv = await conn.fetchval(
                    "SELECT balance FROM scores WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    receiver_id,
                )
                if (recv or 0) + amount > MAX_QP:
                    return "overflow"
                row = await conn.fetchrow(
                    "UPDATE scores SET balance = balance - $3, updated_at = CURRENT_TIMESTAMP "
                    "WHERE guild_id = $1 AND user_id = $2 AND balance >= $3 RETURNING balance",
                    guild_id,
                    sender_id,
                    amount,
                )
                if row is None:
                    return "insufficient"
                await conn.execute(
                    "INSERT INTO scores (guild_id, user_id, balance) VALUES ($1, $2, $3) "
                    "ON CONFLICT (guild_id, user_id) "
                    "DO UPDATE SET balance = balance + $3, updated_at = CURRENT_TIMESTAMP",
                    guild_id,
                    receiver_id,
                    amount,
                )
                return "ok"

    async def add_qp(self, guild_id: int, user_id: int, amount: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO scores (guild_id, user_id, balance) VALUES ($1, $2, MIN($3, $4)) "
            "ON CONFLICT (guild_id, user_id) "
            "DO UPDATE SET balance = MIN(balance + $3, $4), updated_at = CURRENT_TIMESTAMP "
            "RETURNING balance",
            guild_id,
            user_id,
            amount,
            MAX_QP,
        )

    async def sub_qp(self, guild_id: int, user_id: int, amount: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO scores (guild_id, user_id, balance) VALUES ($1, $2, 0) "
            "ON CONFLICT (guild_id, user_id) "
            "DO UPDATE SET balance = MAX(balance - $3, 0), updated_at = CURRENT_TIMESTAMP "
            "RETURNING balance",
            guild_id,
            user_id,
            amount,
        )

    async def set_balance(self, guild_id: int, user_id: int, amount: int) -> int:
        amount = max(0, min(amount, MAX_QP))
        return await self.pool.fetchval(
            "INSERT INTO scores (guild_id, user_id, balance) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) "
            "DO UPDATE SET balance = excluded.balance, updated_at = CURRENT_TIMESTAMP RETURNING balance",
            guild_id,
            user_id,
            amount,
        )

    async def reset_guild(self, guild_id: int) -> None:
        await self.pool.execute("DELETE FROM scores WHERE guild_id = $1", guild_id)
