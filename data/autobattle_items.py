"""Autobattle items: equippable gear loaded from data/autobattle_items.json. This module only
loads + serves the data; the engine applies an item's combat effects on its trigger
(on_enter / on_hurt / on_debuff). Ported from the legacy autochess item system. Players buy items
with QP (/ab shop) and equip them per servant (/ab equip); ownership + equips live in
services/contracts.py, and the /ab fight flow spends one copy per fight an item is equipped for.
"""
from __future__ import annotations

import json
from pathlib import Path

ITEMS_PATH = Path(__file__).resolve().parent / "autobattle_items.json"
_cache: "dict | None" = None


def load_items() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(ITEMS_PATH.read_text(encoding="utf-8")) if ITEMS_PATH.exists() else {}
    return _cache


def get_item(item_id: "str | None") -> "dict | None":
    if not item_id:
        return None
    return load_items().get(item_id)


def all_items() -> dict:
    return dict(load_items())


def items_by_trigger(trigger: str) -> dict:
    return {k: v for k, v in load_items().items() if v.get("trigger") == trigger}
