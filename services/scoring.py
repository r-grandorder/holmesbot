from __future__ import annotations

import asyncpg

from branding import MAX_QP


class ScoringService:
    """QP economy. `points` = lifetime QP earned (leaderboard, monotonic).
    `balance` = spendable QP (capped at MAX_QP, moved by /pay). A win raises both.

    Amount/cap params are cast to ::bigint: they appear in both LEAST/GREATEST and
    arithmetic, and bare parameters there are otherwise ambiguous (text vs bigint)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def award(self, guild_id: int, user_id: int, amount: int) -> int:
        """Award QP for a win; returns the new (capped) balance."""
        return await self.pool.fetchval(
            """
            INSERT INTO scores (guild_id, user_id, points, balance, wins, games)
            VALUES ($1, $2, $3::bigint, LEAST($3::bigint, $4::bigint), 1, 1)
            ON CONFLICT (guild_id, user_id) DO UPDATE
            SET points = scores.points + $3::bigint,
                balance = LEAST(scores.balance + $3::bigint, $4::bigint),
                wins = scores.wins + 1,
                games = scores.games + 1,
                updated_at = now()
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

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[asyncpg.Record]:
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
                    "UPDATE scores SET balance = balance - $3::bigint, updated_at = now() "
                    "WHERE guild_id = $1 AND user_id = $2 AND balance >= $3::bigint RETURNING balance",
                    guild_id,
                    sender_id,
                    amount,
                )
                if row is None:
                    return "insufficient"
                await conn.execute(
                    "INSERT INTO scores (guild_id, user_id, balance) VALUES ($1, $2, $3::bigint) "
                    "ON CONFLICT (guild_id, user_id) "
                    "DO UPDATE SET balance = scores.balance + $3::bigint, updated_at = now()",
                    guild_id,
                    receiver_id,
                    amount,
                )
                return "ok"

    async def add_qp(self, guild_id: int, user_id: int, amount: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO scores (guild_id, user_id, balance) VALUES ($1, $2, LEAST($3::bigint, $4::bigint)) "
            "ON CONFLICT (guild_id, user_id) "
            "DO UPDATE SET balance = LEAST(scores.balance + $3::bigint, $4::bigint), updated_at = now() "
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
            "DO UPDATE SET balance = GREATEST(scores.balance - $3::bigint, 0), updated_at = now() "
            "RETURNING balance",
            guild_id,
            user_id,
            amount,
        )

    async def set_balance(self, guild_id: int, user_id: int, amount: int) -> int:
        amount = max(0, min(amount, MAX_QP))
        return await self.pool.fetchval(
            "INSERT INTO scores (guild_id, user_id, balance) VALUES ($1, $2, $3::bigint) "
            "ON CONFLICT (guild_id, user_id) "
            "DO UPDATE SET balance = EXCLUDED.balance, updated_at = now() RETURNING balance",
            guild_id,
            user_id,
            amount,
        )

    async def reset_guild(self, guild_id: int) -> None:
        await self.pool.execute("DELETE FROM scores WHERE guild_id = $1", guild_id)
