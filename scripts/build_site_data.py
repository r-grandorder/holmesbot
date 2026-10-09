"""Generate web/kits.json for the static kit-browser site (Cloudflare Pages).

Reads the per-file kit sources (data/kits/*.json, committed) and, when available, enriches each
record with the servant's rarity + face portrait from the servant data (the generated NA index or
the Atlas export, plus the committed JP-only index). Pure static reference data -- no DB, no auth;
the site filters it entirely client-side. Run on each Pages deploy (see web/README.md). Works from
the kit files alone if servant data isn't present.

Only kitted servants are in here. The page learns about the rest (servants the weekly Atlas refresh
added that have no kit yet) from the bot's live /api/servants endpoint, which is what feeds the
mod-only Add kit picker.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KITS_DIR = ROOT / "data" / "kits"
OUT = ROOT / "web" / "kits.json"
ATLAS_NA_BASIC = "https://api.atlasacademy.io/export/NA/basic_servant.json"


def _servants() -> dict:
    """id -> servant record (for name/class/rarity/face). Custom + NPC units come from the committed
    hand-curated files; NA servants come from the generated servants.json when present, else the
    Atlas basic-servant export fetched over stdlib urllib (so a Cloudflare Pages build needs only
    Python -- no sync, no deps); JP-only servants come from the committed servants_jp.json. Falls
    back to kit data alone if Atlas is unreachable."""
    idx: dict[int, dict] = {}
    for fn in ("custom_servants.json", "npc_servants.json"):
        p = ROOT / "data" / fn
        if p.exists():
            for s in json.loads(p.read_text(encoding="utf-8")):
                idx[int(s["id"])] = s
    na = ROOT / "data" / "servants.json"
    if na.exists():
        for s in json.loads(na.read_text(encoding="utf-8")):
            idx[int(s["id"])] = s
    else:
        try:
            with urllib.request.urlopen(ATLAS_NA_BASIC, timeout=30) as r:
                for s in json.loads(r.read()):
                    idx[int(s["id"])] = s
            print("enriched NA faces/rarity from the Atlas basic export")
        except Exception as e:  # site still builds from kit data alone
            print(f"Atlas fetch failed ({e}); building without NA face art")
    # JP-only servants are committed (the weekly refresh rewrites the file), so they are always on
    # hand. NA wins a dup: a servant that graduated to NA since the last JP refresh keeps its NA
    # record, mirroring data.servants.ServantIndex.load.
    jp = ROOT / "data" / "servants_jp.json"
    if jp.exists():
        for s in json.loads(jp.read_text(encoding="utf-8")):
            idx.setdefault(int(s["id"]), s)
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
