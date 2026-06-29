"""Build data/servants.json: a trimmed servant index from Atlas Academy.

Fetches the NA nice_servant_lore export (the lore variant carries each servant's
profile, which is where the CV/seiyuu lives), keeps playable servants that have
charaGraph art (full illustrations) and/or charaFigure sprites (battle figures,
used for the silhouette game), and trims to the fields the games need. Re-run to
refresh.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REGION = "NA"
EXPORT_URL = f"https://api.atlasacademy.io/export/{REGION}/nice_servant_lore.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "servants.json"
PLAYABLE_TYPES = {"normal", "heroine"}


def main() -> int:
    print(f"Fetching {EXPORT_URL} ...", flush=True)
    with urllib.request.urlopen(EXPORT_URL) as resp:
        servants = json.load(resp)
    print(f"  {len(servants)} servant records", flush=True)

    trimmed = []
    for s in servants:
        if s.get("type") not in PLAYABLE_TYPES:
            continue
        extra = s.get("extraAssets", {})
        art = {str(k): v for k, v in extra.get("charaGraph", {}).get("ascension", {}).items() if v}
        figure = {str(k): v for k, v in extra.get("charaFigure", {}).get("ascension", {}).items() if v}
        if not art and not figure:
            continue
        faces = extra.get("faces", {}).get("ascension", {})
        face = faces.get("1") or next(iter(faces.values()), None)
        trimmed.append(
            {
                "id": s["id"],
                "name": s["name"],
                "className": s.get("className", ""),
                "rarity": s.get("rarity", 0),
                "art": art,
                "figure": figure,
                "face": face,
                "cv": ((s.get("profile") or {}).get("cv") or "").strip() or None,
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(trimmed, ensure_ascii=False))
    print(f"Wrote {len(trimmed)} servants to {OUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
