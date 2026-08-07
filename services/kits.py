"""Live, web-editable kit overrides.

Overrides live in SQLite (kit_overrides) and are layered onto the in-memory KitIndex, so a mod's
edit applies within seconds without an image rebuild. The git data/kits/*.json files remain the
seed/archive. Validation is the shared data.kits.validate_kit (same checks the build compiler runs),
so the web can't ship a battle-breaking kit.
"""
from __future__ import annotations

import json


class KitService:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def all_overrides(self) -> "list[tuple[int, dict]]":
        rows = await self.pool.fetch("SELECT servant_id, kit_json FROM kit_overrides")
        return [(r["servant_id"], json.loads(r["kit_json"])) for r in rows]

    async def get_override(self, servant_id: int) -> "dict | None":
        raw = await self.pool.fetchval(
            "SELECT kit_json FROM kit_overrides WHERE servant_id = $1", servant_id
        )
        return json.loads(raw) if raw else None

    async def set_override(self, servant_id: int, kit: dict, editor_id: int) -> None:
        await self.pool.execute(
            "INSERT INTO kit_overrides (servant_id, kit_json, updated_by, updated_at) "
            "VALUES ($1, $2, $3, CURRENT_TIMESTAMP) "
            "ON CONFLICT (servant_id) DO UPDATE SET kit_json = $2, updated_by = $3, "
            "updated_at = CURRENT_TIMESTAMP",
            servant_id, json.dumps(kit), editor_id,
        )

    async def delete_override(self, servant_id: int) -> None:
        await self.pool.execute("DELETE FROM kit_overrides WHERE servant_id = $1", servant_id)

    async def audit(self, actor_id: int, action: str, detail: dict) -> None:
        await self.pool.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES ($1, $2, $3)",
            actor_id, action, json.dumps(detail),
        )
