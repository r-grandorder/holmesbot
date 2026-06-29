from __future__ import annotations

import datetime as dt

import asyncpg


class GameService:
    """Durable round state. Rounds live in `active_games`, not process memory, so a
    deploy or crash can't strand them; resolution is idempotent (status-guarded)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def open_round(
        self,
        *,
        guild_id: int,
        channel_id: int,
        game_type: str,
        servant_id: int,
        ascension: str | None,
        answer_name: str,
        points: int,
        started_by: int,
        expires_at: dt.datetime,
    ) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO active_games
              (guild_id, channel_id, game_type, servant_id, ascension,
               answer_name, points, started_by, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id
            """,
            guild_id,
            channel_id,
            game_type,
            servant_id,
            ascension,
            answer_name,
            points,
            started_by,
            expires_at,
        )

    async def attach_message(self, game_id: int, message_id: int) -> None:
        await self.pool.execute(
            "UPDATE active_games SET message_id = $2 WHERE id = $1", game_id, message_id
        )

    async def resolve(
        self,
        game_id: int,
        outcome: str,
        winner_id: int | None,
        points_awarded: int,
    ) -> bool:
        """Resolve a round once. Returns False if it was already resolved."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "UPDATE active_games SET status = 'resolved' "
                    "WHERE id = $1 AND status = 'active' RETURNING *",
                    game_id,
                )
                if row is None:
                    return False
                await conn.execute(
                    """
                    INSERT INTO game_history
                      (guild_id, channel_id, game_type, servant_id, ascension,
                       winner_id, points_awarded, outcome)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    row["guild_id"],
                    row["channel_id"],
                    row["game_type"],
                    row["servant_id"],
                    row["ascension"],
                    winner_id,
                    points_awarded,
                    outcome,
                )
                return True

    async def sweep_expired(self) -> None:
        await self.pool.execute(
            "UPDATE active_games SET status = 'expired' "
            "WHERE status = 'active' AND expires_at < now()"
        )

    async def close_all_active(self) -> list[asyncpg.Record]:
        """Mark every still-active round superseded; return their rows. Used once on
        startup to tidy rounds orphaned by a restart (their in-memory handler/timer
        is gone, so the prompt would otherwise sit forever showing 'type the name')."""
        return await self.pool.fetch(
            "UPDATE active_games SET status = 'superseded' WHERE status = 'active' "
            "RETURNING channel_id, message_id, answer_name"
        )
