"""Validate + compile the per-servant kit files (data/kits/*.json) into data/kits.json.

Run via `make kits`; also run at Docker build. FAILS on any schema error (bad effect_type /
target / trigger, duplicate id, missing/typed field) so a hand-authoring typo can't ship and
silently break a battle. WARNS (does not fail) on kits whose id isn't in the servant data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from data.kits import EFFECT_TYPES, TARGETS, TRIGGERS  # noqa: E402

KITS_DIR = ROOT / "data" / "kits"
OUT = ROOT / "data" / "kits.json"


def _known_servant_ids() -> "set[int]":
    ids: set[int] = set()
    for fn in ("servants.json", "servants_jp.json", "custom_servants.json", "npc_servants.json"):
        p = ROOT / "data" / fn
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else list(data.values())
            ids.update(int(s["id"]) for s in rows if "id" in s)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return ids


def main() -> int:
    if not KITS_DIR.exists():
        print(f"no kit directory at {KITS_DIR}", file=sys.stderr)
        return 1

    errors: list[str] = []
    warnings: list[str] = []
    compiled: dict[str, dict] = {}
    seen: dict[int, str] = {}
    known = _known_servant_ids()

    for fn in sorted(KITS_DIR.glob("*.json")):
        try:
            kit = json.loads(fn.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{fn.name}: invalid JSON ({exc})")
            continue

        missing = [k for k in ("id", "name", "trigger", "effects") if k not in kit]
        if missing:
            errors.append(f"{fn.name}: missing {missing}")
            continue

        sid = kit["id"]
        if not isinstance(sid, int) or isinstance(sid, bool):
            errors.append(f"{fn.name}: id must be an int, got {sid!r}")
            continue
        if sid in seen:
            errors.append(f"{fn.name}: duplicate id {sid} (also {seen[sid]})")
            continue
        seen[sid] = fn.name

        if kit["trigger"] not in TRIGGERS:
            errors.append(f"{fn.name}: bad trigger {kit['trigger']!r}")
        if not isinstance(kit["effects"], list) or not kit["effects"]:
            errors.append(f"{fn.name}: 'effects' must be a non-empty list")
        else:
            for i, e in enumerate(kit["effects"]):
                loc = f"{fn.name} effect[{i}]"
                if e.get("effect_type") not in EFFECT_TYPES:
                    errors.append(f"{loc}: bad effect_type {e.get('effect_type')!r}")
                if e.get("target") not in TARGETS:
                    errors.append(f"{loc}: bad target {e.get('target')!r}")
                if not isinstance(e.get("value"), (int, float)) or isinstance(e.get("value"), bool):
                    errors.append(f"{loc}: value must be a number")
                if not isinstance(e.get("duration"), int) or isinstance(e.get("duration"), bool):
                    errors.append(f"{loc}: duration must be an int")

        if known and sid not in known:
            warnings.append(f"{fn.name}: id {sid} not in servant data (kit will go unused)")
        compiled[str(sid)] = kit

    for w in warnings:
        print(f"WARN {w}", file=sys.stderr)
    if errors:
        print(f"\n{len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ERROR {e}", file=sys.stderr)
        return 1

    OUT.write_text(json.dumps(compiled, ensure_ascii=False), encoding="utf-8")
    tail = f"  ({len(warnings)} warning(s))" if warnings else ""
    print(f"compiled {len(compiled)} kits -> {OUT}{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
