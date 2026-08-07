"""Raid persistence: staff-authored definitions (web-editable), live shared-HP instances, per-user
contributions, uploaded sprite BLOBs, and history. Mirrors services/kits.py (config CRUD + audit) and
services/wars.py (lifecycle + leaderboard). All damage/HP writes go through one serialized db.py
connection, so the read-modify-write in apply_damage is atomic without an explicit row lock.
"""
from __future__ import annotations

import json


class RaidService:
    def __init__(self, pool) -> None:
        self.pool = pool

    # ---- definitions (website config) ----
    async def all_defs(self) -> "list[tuple[str, dict, bool]]":
        rows = await self.pool.fetch(
            "SELECT name, def_json, enabled FROM raid_defs ORDER BY name"
        )
        return [(r["name"], json.loads(r["def_json"]), bool(r["enabled"])) for r in rows]

    async def get_def(self, name: str) -> "tuple[dict, bool] | None":
        row = await self.pool.fetchrow(
            "SELECT def_json, enabled FROM raid_defs WHERE name = $1", name
        )
        return (json.loads(row["def_json"]), bool(row["enabled"])) if row else None

    async def set_def(self, name: str, defn: dict, editor_id: int) -> None:
        await self.pool.execute(
            "INSERT INTO raid_defs (name, def_json, updated_by, updated_at) "
            "VALUES ($1, $2, $3, CURRENT_TIMESTAMP) "
            "ON CONFLICT (name) DO UPDATE SET def_json = $2, updated_by = $3, "
            "updated_at = CURRENT_TIMESTAMP",  # leaves `enabled` untouched
            name, json.dumps(defn), editor_id,
        )

    async def delete_def(self, name: str) -> None:
        await self.pool.execute("DELETE FROM raid_defs WHERE name = $1", name)

    async def set_enabled(self, name: str, on: bool, editor_id: int) -> bool:
        got = await self.pool.fetchval(
            "UPDATE raid_defs SET enabled = $2, updated_by = $3, updated_at = CURRENT_TIMESTAMP "
            "WHERE name = $1 RETURNING name",
            name, 1 if on else 0, editor_id,
        )
        return got is not None

    # ---- live instance lifecycle ----
    async def active(self, guild_id: int) -> "dict | None":
        row = await self.pool.fetchrow(
            "SELECT * FROM raid_instances WHERE guild_id = $1 AND status = 'active'", guild_id
        )
        return dict(row) if row else None

    async def start(
        self, guild_id: int, def_name: str, display_name: str, boss_id: int,
        total_hp: int, expires_at: str, channel_id: "int | None" = None,
    ) -> "int | None":
        """Open a raid. Returns the new instance id, or None if one is already active."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT id FROM raid_instances WHERE guild_id = $1 AND status = 'active'",
                    guild_id,
                )
                if existing is not None:
                    return None
                return await conn.fetchval(
                    "INSERT INTO raid_instances (guild_id, def_name, display_name, boss_id, "
                    "current_hp, total_hp, expires_at, channel_id) "
                    "VALUES ($1, $2, $3, $4, $5, $5, $6, $7) RETURNING id",
                    guild_id, def_name, display_name, boss_id, total_hp, expires_at, channel_id,
                )

    async def apply_damage(self, instance_id: int, user_id: int, dmg: int) -> "tuple[int, bool]":
        """Subtract damage from the shared pool + record the contribution, atomically. Returns
        (remaining_hp, defeated). Returns (-1, False) if the instance is not active."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT current_hp, status FROM raid_instances WHERE id = $1", instance_id
                )
                if row is None or row["status"] != "active":
                    return -1, False
                new_hp = max(0, row["current_hp"] - max(0, dmg))
                defeated = new_hp <= 0
                await conn.execute(
                    "UPDATE raid_instances SET current_hp = $2, "
                    "status = CASE WHEN $2 <= 0 THEN 'defeated' ELSE 'active' END, "
                    "last_hit_by = CASE WHEN $2 <= 0 THEN $3 ELSE last_hit_by END, "
                    "ended_at = CASE WHEN $2 <= 0 THEN CURRENT_TIMESTAMP ELSE ended_at END "
                    "WHERE id = $1",
                    instance_id, new_hp, user_id,
                )
                await conn.execute(
                    "INSERT INTO raid_participation (instance_id, user_id, damage, attempts, last_at) "
                    "VALUES ($1, $2, $3, 1, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (instance_id, user_id) DO UPDATE SET "
                    "damage = damage + $3, attempts = attempts + 1, last_at = CURRENT_TIMESTAMP",
                    instance_id, user_id, max(0, dmg),
                )
                return new_hp, defeated

    async def end(self, instance_id: int, reason: str = "expired", last_hit_by: "int | None" = None) -> None:
        await self.pool.execute(
            "UPDATE raid_instances SET status = $2, ended_at = CURRENT_TIMESTAMP, "
            "last_hit_by = COALESCE($3, last_hit_by) WHERE id = $1 AND status = 'active'",
            instance_id, reason, last_hit_by,
        )

    async def expired(self) -> "list[dict]":
        """Active instances whose deadline has passed (for the expiry ticker)."""
        rows = await self.pool.fetch(
            "SELECT * FROM raid_instances WHERE status = 'active' AND expires_at <= CURRENT_TIMESTAMP"
        )
        return [dict(r) for r in rows]

    # ---- status + history ----
    async def leaderboard(self, instance_id: int, limit: int = 10) -> "list":
        return await self.pool.fetch(
            "SELECT user_id, damage, attempts FROM raid_participation "
            "WHERE instance_id = $1 ORDER BY damage DESC LIMIT $2",
            instance_id, limit,
        )

    async def all_participants(self, instance_id: int) -> "list":
        return await self.pool.fetch(
            "SELECT user_id, damage, attempts, rewarded FROM raid_participation "
            "WHERE instance_id = $1 ORDER BY damage DESC",
            instance_id,
        )

    async def participation(self, instance_id: int, user_id: int) -> "dict | None":
        row = await self.pool.fetchrow(
            "SELECT damage, attempts FROM raid_participation WHERE instance_id = $1 AND user_id = $2",
            instance_id, user_id,
        )
        return dict(row) if row else None

    async def history(self, guild_id: int, limit: int = 25) -> "list":
        return await self.pool.fetch(
            "SELECT id, def_name, display_name, boss_id, total_hp, status, started_at, ended_at "
            "FROM raid_instances WHERE guild_id = $1 AND status != 'active' "
            "ORDER BY id DESC LIMIT $2",
            guild_id, limit,
        )

    async def instance(self, instance_id: int) -> "dict | None":
        row = await self.pool.fetchrow("SELECT * FROM raid_instances WHERE id = $1", instance_id)
        return dict(row) if row else None

    async def mark_rewarded(self, instance_id: int, user_id: int) -> bool:
        """Flip the payout guard on; returns True only if it was newly set (so rewards pay once)."""
        got = await self.pool.fetchval(
            "UPDATE raid_participation SET rewarded = 1 "
            "WHERE instance_id = $1 AND user_id = $2 AND rewarded = 0 RETURNING user_id",
            instance_id, user_id,
        )
        return got is not None

    # ---- uploaded sprites (BLOBs served over the tunnel) ----
    async def store_sprite(self, image: bytes, uploader_id: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO raid_sprites (image, uploaded_by) VALUES ($1, $2) RETURNING id",
            image, uploader_id,
        )

    async def get_sprite(self, sprite_id: int) -> "bytes | None":
        return await self.pool.fetchval(
            "SELECT image FROM raid_sprites WHERE id = $1", sprite_id
        )

    # ---- audit ----
    async def audit(self, actor_id: int, action: str, detail: dict) -> None:
        await self.pool.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES ($1, $2, $3)",
            actor_id, action, json.dumps(detail),
        )
