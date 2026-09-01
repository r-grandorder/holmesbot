"""Live, web-editable custom summonable servants.

Definitions live in SQLite (custom_servants) and are layered onto the in-memory ServantIndex,
so a mod's edit applies within seconds without an image rebuild. The git
data/custom_servants.json file remains the seed/archive. Validation is the shared
data.custom_servants.validate_custom_servant, so the web can't ship a unit that shadows a real
servant or breaks guess matching.

Mirrors services/kits.py (config CRUD + audit).
"""
from __future__ import annotations

import json


class CustomServantService:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def all(self) -> "list[tuple[int, dict, bool]]":
        rows = await self.pool.fetch(
            "SELECT id, def_json, enabled FROM custom_servants ORDER BY id DESC"
        )
        return [(r["id"], json.loads(r["def_json"]), bool(r["enabled"])) for r in rows]

    async def enabled_defs(self) -> "list[dict]":
        """Only the units a mod has switched on. A disabled row stays out of the index
        entirely, so a half-built servant can't be summoned or block a name."""
        rows = await self.pool.fetch(
            "SELECT def_json FROM custom_servants WHERE enabled = 1 ORDER BY id DESC"
        )
        return [json.loads(r["def_json"]) for r in rows]

    async def get(self, servant_id: int) -> "tuple[dict, bool] | None":
        row = await self.pool.fetchrow(
            "SELECT def_json, enabled FROM custom_servants WHERE id = $1", servant_id
        )
        return (json.loads(row["def_json"]), bool(row["enabled"])) if row else None

    async def upsert(
        self, servant_id: int, defn: dict, editor_id: int, *, enable_on_create: bool = False
    ) -> None:
        """Create or update a definition. `enabled` is only ever set on INSERT -- an update
        leaves it alone, so saving an edit to a live unit can't silently pull it out of the
        summon pool.

        enable_on_create is for ADOPTING a file-authored custom: that unit is already live in
        the index, so writing its first DB row as a draft would quietly un-summon it."""
        await self.pool.execute(
            "INSERT INTO custom_servants (id, def_json, enabled, updated_by, updated_at) "
            "VALUES ($1, $2, $4, $3, CURRENT_TIMESTAMP) "
            "ON CONFLICT (id) DO UPDATE SET def_json = $2, updated_by = $3, "
            "updated_at = CURRENT_TIMESTAMP",
            servant_id, json.dumps(defn), editor_id, 1 if enable_on_create else 0,
        )

    async def set_enabled(self, servant_id: int, enabled: bool, editor_id: int) -> None:
        await self.pool.execute(
            "UPDATE custom_servants SET enabled = $2, updated_by = $3, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = $1",
            servant_id, 1 if enabled else 0, editor_id,
        )

    async def delete(self, servant_id: int) -> None:
        await self.pool.execute("DELETE FROM custom_servants WHERE id = $1", servant_id)

    async def next_id(self, floor: int = 0) -> int:
        """Allocate a fresh custom id: one below the lowest already in use. `floor` is the
        lowest id the live index knows about, which the caller reads off ServantIndex --
        JSON-authored customs never appear in this table, so without it a new unit could be
        handed an id that already belongs to one of them."""
        db_min = await self.pool.fetchval("SELECT MIN(id) FROM custom_servants")
        lowest = min(x for x in (db_min, floor, 0) if x is not None)
        return int(lowest) - 1

    async def audit(self, actor_id: int, action: str, detail: dict) -> None:
        await self.pool.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES ($1, $2, $3)",
            actor_id, action, json.dumps(detail),
        )
