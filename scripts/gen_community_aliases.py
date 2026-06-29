"""Generate a dbmate seed migration of community servant nicknames.

One-time dev tool (re-run to refresh). Reads a vendored nickname list
(scripts/data/nicknames.json, keyed by Atlas collectionNo), maps collectionNo ->
Atlas internal id, normalizes each alias with the SAME normalize() the bot's
matcher uses, drops obvious junk (class names, meme/generic/edgy terms, run-on
jokes, single chars), and writes a migration that upserts the survivors into
servant_aliases (ON CONFLICT DO NOTHING) so AliasService.reload() picks them up.

Source list: Lutrec/FGO-Damage-Calculator (data/nicknames.json), vendored at
scripts/data/nicknames.json. collectionNo -> id is resolved from the live Atlas
NA basic_servant export, intersected with our own data/servants.json so we only
emit aliases for servants the bot actually knows.

    python scripts/gen_community_aliases.py
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NICKNAMES = ROOT / "scripts" / "data" / "nicknames.json"
SERVANTS = ROOT / "data" / "servants.json"
MIGRATION = ROOT / "database" / "migrations" / "20260628000001_seed_community_aliases.sql"
REVIEW = ROOT / "scripts" / "data" / "community_aliases_review.txt"
BASIC_EXPORT = "https://api.atlasacademy.io/export/NA/basic_servant.json"

# --- normalize: an EXACT copy of data.matching.normalize, inlined so this script
# runs without the bot's deps (matching.py imports rapidfuzz). Keep in sync. ---
_KEEP = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    base = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    base = _WS.sub(" ", _KEEP.sub(" ", base)).strip()
    return base.replace(" ", "")


# --- conservative junk filter ---
CLASS_NAMES = {
    "saber", "archer", "lancer", "rider", "caster", "assassin", "berserker",
    "ruler", "avenger", "alterego", "mooncancer", "foreigner", "pretender",
    "shielder", "beast",
}
# Clearly generic/fandom/edgy terms that would cause false wins and pollute the
# wrong-guess detector (all_terms). Exact-match only, so it never blocks substrings
# like "redsaber" or "bestgirleli".
MEME_BLOCKLIST = {
    "waifu", "husbando", "bestgirl", "bestboy", "best", "fgo", "grandorder",
    "meme", "cute", "smol", "gay", "trap", "sex", "thicc", "mommy", "loli", "shota",
}
MAX_LEN = 20  # run-on jokes, e.g. "morecostumesthaneveryone" (24)
MIN_LEN = 2   # single-char aliases are dangerous (false wins)


def junk_reason(norm: str, self_name_norm: str) -> str | None:
    if len(norm) < MIN_LEN:
        return "too-short"
    if norm == self_name_norm:  # before the length check: long names aren't junk
        return "==name (redundant)"
    if norm in CLASS_NAMES:
        return "class-name"
    if norm in MEME_BLOCKLIST:
        return "meme/generic"
    if len(norm) > MAX_LEN:
        return "too-long"
    return None


def fetch_colno_to_id() -> dict[int, int]:
    print(f"Fetching {BASIC_EXPORT} ...", flush=True)
    with urllib.request.urlopen(BASIC_EXPORT) as resp:
        data = json.load(resp)
    m = {s["collectionNo"]: s["id"] for s in data if s.get("collectionNo")}
    print(f"  {len(m)} collectionNo->id pairs", flush=True)
    return m


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main() -> int:
    nicknames = json.loads(NICKNAMES.read_text())
    servants = json.loads(SERVANTS.read_text())
    by_id = {s["id"]: s for s in servants}  # the servants the bot actually knows
    colno_to_id = fetch_colno_to_id()

    rows: list[tuple[int, str, str]] = []      # (servant_id, alias_display, norm)
    seen: set[tuple[int, str]] = set()         # (servant_id, norm) dedup
    dropped: list[tuple[int, str, str]] = []   # (servant_id, alias, reason)
    unmatched: list[tuple[str, list]] = []     # keys with no known servant
    norm_owners: dict[str, set[int]] = {}      # norm -> servant_ids (collision report)

    for key, aliases in nicknames.items():
        k = int(key)
        sid = colno_to_id.get(k)            # primary: key is a collectionNo
        if sid is None and k in by_id:      # fallback: key is already an internal id
            sid = k
        if sid is None or sid not in by_id:
            unmatched.append((key, aliases))
            continue
        self_name_norm = normalize(by_id[sid]["name"])
        for alias in aliases:
            norm = normalize(alias)
            reason = junk_reason(norm, self_name_norm)
            if reason:
                dropped.append((sid, alias, reason))
                continue
            if (sid, norm) in seen:
                continue
            seen.add((sid, norm))
            rows.append((sid, alias, norm))
            norm_owners.setdefault(norm, set()).add(sid)

    rows.sort()
    up_vals = ",\n".join(f"  ({s}, {sql_str(a)}, {sql_str(n)})" for s, a, n in rows)
    down_vals = ",\n".join(f"  ({s}, {sql_str(n)})" for s, _, n in rows)
    migration = (
        "-- migrate:up\n"
        "-- Community servant nicknames, bulk-imported from the Lutrec/FGO-Damage-\n"
        "-- Calculator nickname list, mapped collectionNo->id and conservatively\n"
        "-- de-junked. Generated by scripts/gen_community_aliases.py; do not edit by hand.\n"
        "INSERT INTO servant_aliases (servant_id, alias, norm) VALUES\n"
        f"{up_vals}\n"
        "ON CONFLICT (servant_id, norm) DO NOTHING;\n\n"
        "-- migrate:down\n"
        "DELETE FROM servant_aliases\n"
        f"WHERE (servant_id, norm) IN (\n{down_vals}\n);\n"
    )
    MIGRATION.write_text(migration)

    # --- report ---
    total_in = sum(len(v) for v in nicknames.values())
    print(f"\ninput aliases: {total_in} across {len(nicknames)} keys")
    print(f"KEPT {len(rows)} aliases for {len({r[0] for r in rows})} servants")
    dc = Counter(r[2] for r in dropped)
    print(f"DROPPED {len(dropped)}: " + ", ".join(f"{k}={v}" for k, v in dc.most_common()))
    print(f"UNMATCHED keys (no known NA servant): {len(unmatched)}")
    for key, al in unmatched:
        print(f"    {key}: {al}")
    collisions = {n: ids for n, ids in norm_owners.items() if len(ids) > 1}
    print(f"\nCROSS-SERVANT duplicate terms (genericness signal): {len(collisions)}")
    for n, ids in sorted(collisions.items(), key=lambda x: -len(x[1]))[:30]:
        print(f"    {n!r} -> {len(ids)} servants")
    print("\nsample DROPPED (sanity check the filter):")
    for s, a, reason in dropped[:40]:
        print(f"    [{reason}] {by_id[s]['name']}: {a!r}")
    print(f"\nwrote {MIGRATION.relative_to(ROOT)}")

    # --- human-readable review file ---
    kept_by_sid: dict[int, list[str]] = defaultdict(list)
    for s, a, _ in rows:
        kept_by_sid[s].append(a)
    judgment: dict[str, list[tuple[str, str]]] = defaultdict(list)
    redundant: list[tuple[str, str]] = []
    for s, a, reason in dropped:
        if reason.startswith("==name"):
            redundant.append((by_id[s]["name"], a))
        else:
            judgment[reason].append((by_id[s]["name"], a))
    out = [f"=== KEPT: {len(rows)} aliases for {len(kept_by_sid)} servants ==="]
    for s in sorted(kept_by_sid, key=lambda x: by_id[x]["name"].lower()):
        out.append(f"{by_id[s]['name']} [{s}]: " + ", ".join(sorted(kept_by_sid[s])))
    njudg = sum(len(v) for v in judgment.values())
    out += ["", f"=== DROPPED, judgment calls (review these): {njudg} ==="]
    for reason in sorted(judgment):
        out.append(f"[{reason}]")
        out += [f"  {name}: {a!r}" for name, a in sorted(judgment[reason])]
    out += ["", f"=== DROPPED, redundant alias==name (safe, FYI): {len(redundant)} ==="]
    out += [f"  {name}: {a!r}" for name, a in sorted(redundant)]
    out += ["", f"=== UNMATCHED keys, servant not in NA index: {len(unmatched)} ==="]
    out += [f"  {key}: {', '.join(al) if al else '(empty)'}" for key, al in unmatched]
    collisions = {n: ids for n, ids in norm_owners.items() if len(ids) > 1}
    out += ["", f"=== CROSS-SERVANT shared terms: {len(collisions)} ==="]
    for n, ids in sorted(collisions.items()):
        out.append(f"  {n}: " + ", ".join(by_id[i]["name"] for i in sorted(ids)))
    REVIEW.write_text("\n".join(out) + "\n")
    print(f"wrote {REVIEW.relative_to(ROOT)} (human-readable review)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
