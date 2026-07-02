from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db import Pool, Row

GAME_COLUMN = {
    "guess_servant": "guess_servant_enabled",
    "guess_shadow": "guess_shadow_enabled",
    "guess_audio": "guess_audio_enabled",
    "guess_skill": "guess_skill_enabled",
}


class GuildConfigService:
    def __init__(self, pool: "Pool") -> None:
        self.pool = pool

    async def get(self, guild_id: int) -> "Row":
        row = await self.pool.fetchrow(
            "SELECT * FROM guild_config WHERE guild_id = $1", guild_id
        )
        if row is None:
            row = await self.pool.fetchrow(
                "INSERT INTO guild_config (guild_id) VALUES ($1) "
                "ON CONFLICT (guild_id) DO UPDATE SET guild_id = excluded.guild_id "
                "RETURNING *",
                guild_id,
            )
        return row

    def game_enabled(self, cfg: "Row", game_type: str) -> bool:
        return bool(cfg["enabled"] and cfg[GAME_COLUMN[game_type]])

    async def is_channel_allowed(self, guild_id: int, channel_id: int) -> bool:
        cfg = await self.get(guild_id)
        allowed = json.loads(cfg["allowed_channel_ids"])
        return not allowed or channel_id in allowed

    async def set_game_enabled(
        self, guild_id: int, game_type: str, enabled: bool
    ) -> None:
        await self.get(guild_id)
        column = GAME_COLUMN[game_type]  # from a fixed map, safe to interpolate
        await self.pool.execute(
            f"UPDATE guild_config SET {column} = $2, updated_at = CURRENT_TIMESTAMP WHERE guild_id = $1",
            guild_id,
            enabled,
        )

    async def add_channel(self, guild_id: int, channel_id: int) -> None:
        cfg = await self.get(guild_id)
        allowed = json.loads(cfg["allowed_channel_ids"])
        if channel_id in allowed:
            return
        allowed.append(channel_id)
        await self.pool.execute(
            "UPDATE guild_config SET allowed_channel_ids = $2, updated_at = CURRENT_TIMESTAMP "
            "WHERE guild_id = $1",
            guild_id,
            json.dumps(allowed),
        )

    async def remove_channel(self, guild_id: int, channel_id: int) -> None:
        cfg = await self.get(guild_id)
        allowed = json.loads(cfg["allowed_channel_ids"])
        if channel_id not in allowed:
            return
        allowed = [c for c in allowed if c != channel_id]
        await self.pool.execute(
            "UPDATE guild_config SET allowed_channel_ids = $2, updated_at = CURRENT_TIMESTAMP "
            "WHERE guild_id = $1",
            guild_id,
            json.dumps(allowed),
        )

    async def clear_channels(self, guild_id: int) -> None:
        await self.pool.execute(
            "UPDATE guild_config SET allowed_channel_ids = '[]', updated_at = CURRENT_TIMESTAMP "
            "WHERE guild_id = $1",
            guild_id,
        )

    async def set_log_channel(self, guild_id: int, channel_id: int | None) -> None:
        await self.get(guild_id)
        await self.pool.execute(
            "UPDATE guild_config SET log_channel_id = $2, updated_at = CURRENT_TIMESTAMP WHERE guild_id = $1",
            guild_id,
            channel_id,
        )
