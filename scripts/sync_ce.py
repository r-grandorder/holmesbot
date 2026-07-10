"""Fetch the NA nice_equip export (Craft Essences) and write data/ce.json for the CE guessing
game. Keeps 4-5* CEs that have charaGraph art -- the recognizable gacha/welfare ones -- and
drops lower-rarity event fodder so the game stays guessable. Mirrors scripts/sync_atlas.py.

    python scripts/sync_ce.py        (or: make sync-ce)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REGION = "NA"
EXPORT_URL = f"https://api.atlasacademy.io/export/{REGION}/nice_equip.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = DATA_DIR / "ce.json"
MIN_RARITY = 5  # 5-star CEs are the recognizable gacha illustrations; lower tiers are mostly
#                 forgettable event fodder. Tunable -- drop to 4 for a much larger, harder pool.


def _first_url(group: dict) -> "str | None":
    """CE assets nest as {'equip': {'<id>': url}} -- return the single url within."""
    for inner in (group or {}).values():
        if isinstance(inner, str):
            return inner or None
        for v in (inner or {}).values():
            if v:
                return v
    return None


def main() -> int:
    print(f"Fetching {EXPORT_URL} ...", flush=True)
    with urllib.request.urlopen(EXPORT_URL) as resp:
        equips = json.load(resp)
    print(f"  {len(equips)} equip records", flush=True)

    trimmed = []
    for e in equips:
        if e.get("type") != "servantEquip" or e.get("rarity", 0) < MIN_RARITY:
            continue
        extra = e.get("extraAssets", {})
        art = _first_url(extra.get("charaGraph", {}))
        if not art:
            continue
        trimmed.append(
            {
                "id": e["id"],
                "name": e["name"],
                "rarity": e.get("rarity", 0),
                "art": {"0": art},
                "face": _first_url(extra.get("faces", {})),
            }
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(trimmed, ensure_ascii=False))
    by_rarity = {r: sum(1 for c in trimmed if c["rarity"] == r) for r in (4, 5)}
    print(f"Wrote {len(trimmed)} craft essences to {OUT_PATH} (by rarity: {by_rarity})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
