"""Generate web/kits.json for the static kit-browser site (Cloudflare Pages).

Reads the per-file kit sources (data/kits/*.json, committed) and, when available, enriches each
record with the servant's rarity + face portrait from the generated servant data. Pure static
reference data -- no DB, no auth; the site filters it entirely client-side. Run on each Pages
deploy (see web/README.md). Works from the kit files alone if servant data isn't present.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KITS_DIR = ROOT / "data" / "kits"
OUT = ROOT / "web" / "kits.json"


def _servants() -> dict:
    idx: dict[int, dict] = {}
    for fn in ("servants.json", "custom_servants.json", "npc_servants.json"):
        p = ROOT / "data" / fn
        if p.exists():
            for s in json.loads(p.read_text(encoding="utf-8")):
                idx[int(s["id"])] = s
    return idx


def main() -> None:
    servants = _servants()
    records = []
    for f in sorted(KITS_DIR.glob("*.json")):
        k = json.loads(f.read_text(encoding="utf-8"))
        s = servants.get(int(k["id"]), {})
        records.append(
            {
                "id": int(k["id"]),
                "name": s.get("name") or k.get("servant_name", ""),
                "className": s.get("className") or k.get("class_name", ""),
                "rarity": s.get("rarity"),
                "face": s.get("face"),
                "skill": k.get("name", ""),
                "description": k.get("description", ""),
                "trigger": k.get("trigger", ""),
                "effects": [
                    {
                        "type": e["effect_type"],
                        "value": e["value"],
                        "duration": e["duration"],
                        "target": e["target"],
                    }
                    for e in k.get("effects", [])
                ],
            }
        )
    records.sort(key=lambda r: r["name"].lower())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    enriched = sum(1 for r in records if r["face"])
    print(f"wrote {len(records)} kit records -> {OUT} ({enriched} with servant art)")


if __name__ == "__main__":
    main()
