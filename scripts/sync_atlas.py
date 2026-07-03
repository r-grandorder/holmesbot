"""Build the servant indexes from Atlas Academy.

Default (NA):
    python scripts/sync_atlas.py
Fetches the NA nice_servant_lore export (the lore variant carries each servant's
profile, where the CV/seiyuu lives), keeps playable servants that have charaGraph
art (full illustrations) and/or charaFigure sprites (battle figures, used for the
silhouette game), and trims to data/servants.json.

JP-only (--jp):
    python scripts/sync_atlas.py --jp
Builds data/servants_jp.json: servants released on JP but not yet in our NA index,
with Atlas's English names plus community nicknames (scripts/data/nicknames.json,
keyed by collectionNo). These load behind the *jp game commands only. Enumerated
from the small basic export and fetched per-id, so it never pulls the 90MB JP lore
bulk. Re-run either mode to refresh.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

REGION = "NA"
EXPORT_URL = f"https://api.atlasacademy.io/export/{REGION}/nice_servant_lore.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = DATA_DIR / "servants.json"
JP_OUT_PATH = DATA_DIR / "servants_jp.json"
PLAYABLE_TYPES = {"normal", "heroine"}

JP_BASIC_URL = "https://api.atlasacademy.io/export/JP/basic_servant_lang_en.json"
JP_NICE_URL = "https://api.atlasacademy.io/nice/JP/servant/{id}?lang=en&lore=true"
NICKNAMES_PATH = Path(__file__).resolve().parent / "data" / "nicknames.json"

# Junk filter for community nicknames (mirrors scripts/gen_community_aliases.py).
_KEEP = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")
CLASS_NAMES = {
    "saber", "archer", "lancer", "rider", "caster", "assassin", "berserker",
    "ruler", "avenger", "alterego", "mooncancer", "foreigner", "pretender",
    "shielder", "beast",
}
MEME_BLOCKLIST = {
    "waifu", "husbando", "bestgirl", "bestboy", "best", "fgo", "grandorder",
    "meme", "cute", "smol", "gay", "trap", "sex", "thicc", "mommy", "loli", "shota",
}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    base = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return _WS.sub(" ", _KEEP.sub(" ", base)).strip().replace(" ", "")


def _assets(extra: dict) -> tuple[dict, dict, str | None]:
    """(art, figure, face) trimmed from an Atlas extraAssets blob."""
    art = {str(k): v for k, v in extra.get("charaGraph", {}).get("ascension", {}).items() if v}
    figure = {str(k): v for k, v in extra.get("charaFigure", {}).get("ascension", {}).items() if v}
    faces = extra.get("faces", {}).get("ascension", {})
    face = faces.get("1") or next(iter(faces.values()), None)
    return art, figure, face


def _nick_aliases(raw: list[str], name: str) -> list[str]:
    """Filter community nicknames the same way gen_community_aliases.py does."""
    self_norm = _normalize(name)
    out: list[str] = []
    seen: set[str] = set()
    for alias in raw:
        norm = _normalize(alias)
        if not norm or norm == self_norm or norm in seen:
            continue
        if norm in CLASS_NAMES or norm in MEME_BLOCKLIST:
            continue
        if len(norm) < 2 or len(norm) > 20:
            continue
        seen.add(norm)
        out.append(alias)
    return out


def _active_skills(record: dict) -> list[dict]:
    """The three active skills (slots 1-3) as [{"num","name","icon"}] in slot order.
    A slot can list several versions: the base kit (priority 1) plus interlude/rank-up
    strengthenings (higher priority) and ascension swaps (higher condLimitCount). Keep
    the LATEST per slot -- max (priority, condLimitCount) -- so we show the strengthened,
    final-ascension skill the servant actually has in-game, not the pre-buff version
    (e.g. Santa Alter's slot 2 is Reindeer Drive, not the base Intuition). In the NA
    export a strengthening only appears once its rank-up quest is released, so this
    stays in step with NA rather than leaking JP-ahead skills."""
    best: dict[int, dict] = {}
    for sk in record.get("skills", []):
        num = sk.get("num")
        if not (isinstance(num, int) and 1 <= num <= 3):
            continue
        if not (sk.get("name") and sk.get("icon")):
            continue
        key = (sk.get("priority", 1), sk.get("condLimitCount", 0))
        cur = best.get(num)
        if cur is None or key > (cur.get("priority", 1), cur.get("condLimitCount", 0)):
            best[num] = sk
    return [{"num": n, "name": best[n]["name"], "icon": best[n]["icon"]} for n in sorted(best)]


def main_na() -> int:
    print(f"Fetching {EXPORT_URL} ...", flush=True)
    with urllib.request.urlopen(EXPORT_URL) as resp:
        servants = json.load(resp)
    print(f"  {len(servants)} servant records", flush=True)

    trimmed = []
    for s in servants:
        if s.get("type") not in PLAYABLE_TYPES:
            continue
        art, figure, face = _assets(s.get("extraAssets", {}))
        if not art and not figure:
            continue
        trimmed.append(
            {
                "id": s["id"],
                "name": s["name"],
                "className": s.get("className", ""),
                "rarity": s.get("rarity", 0),
                "gender": s.get("gender", ""),
                "attribute": s.get("attribute", ""),
                "traits": [t["name"] for t in s.get("traits", []) if t.get("name")],
                "art": art,
                "figure": figure,
                "face": face,
                "cv": ((s.get("profile") or {}).get("cv") or "").strip() or None,
                "skills": _active_skills(s),
            }
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(trimmed, ensure_ascii=False))
    print(f"Wrote {len(trimmed)} servants to {OUT_PATH}", flush=True)
    return 0


def main_jp() -> int:
    if not OUT_PATH.exists():
        print("Build data/servants.json first (run without --jp).", file=sys.stderr)
        return 1
    na_ids = {s["id"] for s in json.loads(OUT_PATH.read_text())}
    nicknames = json.loads(NICKNAMES_PATH.read_text())

    print(f"Fetching {JP_BASIC_URL} ...", flush=True)
    with urllib.request.urlopen(JP_BASIC_URL) as resp:
        basic = json.load(resp)
    jp_only = [
        s
        for s in basic
        if s.get("type") in PLAYABLE_TYPES
        and s["id"] not in na_ids
        and re.search(r"[A-Za-z]", s.get("name", ""))  # skip untranslated (pure JP) names
    ]
    print(f"  {len(jp_only)} JP-only playable servants with English names", flush=True)

    out, skipped = [], []
    for i, s in enumerate(jp_only, 1):
        sid = s["id"]
        try:
            with urllib.request.urlopen(JP_NICE_URL.format(id=sid)) as resp:
                nice = json.load(resp)
        except Exception as e:  # noqa: BLE001 - one bad fetch shouldn't kill the run
            skipped.append((sid, s["name"], f"fetch failed: {e}"))
            continue
        art, figure, face = _assets(nice.get("extraAssets", {}))
        if not art:
            skipped.append((sid, s["name"], "no charaGraph art"))
            continue
        out.append(
            {
                "id": sid,
                "name": s["name"],
                "className": s.get("className", ""),
                "rarity": s.get("rarity", 0),
                "gender": nice.get("gender", ""),
                "attribute": nice.get("attribute", ""),
                "traits": [t["name"] for t in nice.get("traits", []) if t.get("name")],
                "art": art,
                "figure": figure,
                "face": face or s.get("face"),
                "cv": ((nice.get("profile") or {}).get("cv") or "").strip() or None,
                "jp": True,
                "aliases": _nick_aliases(
                    nicknames.get(str(s.get("collectionNo")), []), s["name"]
                ),
                "skills": _active_skills(nice),
            }
        )
        if i % 10 == 0:
            print(f"  ...{i}/{len(jp_only)}", flush=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JP_OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(out)} JP servants to {JP_OUT_PATH}", flush=True)
    if skipped:
        print(f"Skipped {len(skipped)}:", flush=True)
        for sid, name, why in skipped:
            print(f"  {sid} {name}: {why}", flush=True)
    return 0


def main(argv: list[str]) -> int:
    return main_jp() if "--jp" in argv else main_na()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
