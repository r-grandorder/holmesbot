"""Raid config helpers: phase resolution + validation, shared by the raid cog and the web editor.

A raid definition (stored as JSON in raid_defs) looks like:
    {
      "display_name": "Goetia's Siege",
      "boss_servant_id": 9935500,
      "boss_level": 120,
      "total_hp": 5000000,            # shared pool
      "battle_hp": 60000,             # per-battle boss HP (the winnable micro-fight)
      "duration_hours": 24,
      "default_sprite_id": 12,        # optional; else the boss servant's art
      "phases": [                     # optional; kit/name/flavor/sprite swap at HP thresholds
        {"threshold": 1.0, "name": "...", "flavor": "...", "sprite_id": 12,
         "atk_mult": 1.0, "battle_hp": 60000, "class": "berserker",
         "kit": {"name","trigger","effects":[...]}},
        {"threshold": 0.5, "name": "Enraged", ...},
      ],
      "rewards": {
        "per_fight_qp": 500,
        "participation": {"qp": 5000, "embers": 3},
        "ranks": [{"top": 1, "qp": 50000, "grails": 2, "tickets": 1},
                  {"top": 3, "qp": 20000, "grails": 1},
                  {"top": 10, "qp": 10000}],
        "last_hit": {"qp": 25000, "hellfire": 1}
      }
    }
"""
from __future__ import annotations

from data.autobattle import CLASS_NAMES
from data.kits import validate_kit

RAID_FIGHT_COOLDOWN = 20.0      # seconds between a user's raid fights (light anti-spam; no daily cap)
RAID_RANK_LIMIT = 10            # leaderboard size
RAID_EXPIRY_TICK = 5            # minutes between expiry sweeps


def active_phase(defn: dict, current_hp: int, total_hp: int) -> "dict | None":
    """The phase the boss is in at the current pool fraction, or None if it has no phases / is above
    the highest threshold. Phases are entered as the pool drops: the active phase is the one with the
    LOWEST threshold that the current fraction has reached (frac <= threshold)."""
    phases = defn.get("phases") or []
    if not phases or total_hp <= 0:
        return None
    frac = current_hp / total_hp
    match = None
    for ph in sorted(phases, key=lambda p: float(p.get("threshold", 0)), reverse=True):
        if frac <= float(ph.get("threshold", 0)):
            match = ph  # keep the deepest (smallest-threshold) phase reached
    return match


def boss_class(defn: dict, phase: "dict | None") -> str:
    """The boss's class for this phase, lowercased: the phase's own override, else the
    def-wide boss_class, else "" meaning 'keep whatever the boss already is' (the servant's
    real class, or blank for a custom boss).

    Per-phase classes let a boss change class as it is worn down (Saber that goes Berserker
    when enraged), which flips the class-advantage triangle mid-raid, so the team that
    countered the opening phase does not automatically counter the next one."""
    # Strip BEFORE testing, so a blank/whitespace override falls through to the def-wide
    # class rather than reading as "no class" -- validation already treats blank as inherit.
    for value in ((phase or {}).get("class"), defn.get("boss_class")):
        cls = str(value or "").strip().lower()
        if cls:
            return cls
    return ""


def _class_error(value, label: str) -> "str | None":
    """None if `value` is an acceptable class token. Absent or blank means inherit, which is
    always allowed; anything else has to be a name the engine actually knows, since an
    unrecognised class silently degrades to a neutral matchup instead of erroring."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        return f"{label} must be a string"
    if value.strip().lower() not in CLASS_NAMES:
        return f"{label} must be one of: {', '.join(CLASS_NAMES)}"
    return None


def _pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def validate_raid_def(defn: dict) -> "list[str]":
    """Schema-check a raid definition; returns error strings (empty = valid). Phase kits are checked
    with the shared data.kits.validate_kit so a web edit can't ship a battle-breaking boss."""
    errors: list[str] = []
    if not isinstance(defn, dict):
        return ["definition must be an object"]
    for k in ("display_name", "total_hp", "battle_hp", "duration_hours"):
        if k not in defn:
            errors.append(f"missing field: {k}")
    if errors:
        return errors
    if not isinstance(defn["display_name"], str) or not defn["display_name"].strip():
        errors.append("display_name must be a non-empty string")
    # Boss is either an existing servant (borrows its ATK/class/art) OR a fully custom boss with its
    # own boss_atk (+ optional boss_class) and an uploaded sprite.
    sid = defn.get("boss_servant_id")
    if sid is not None and (not isinstance(sid, int) or isinstance(sid, bool) or sid <= 0):
        errors.append("boss_servant_id must be a positive int (or omit it for a custom boss)")
    if not sid and not _pos_int(defn.get("boss_atk")):
        errors.append("a custom boss (no boss_servant_id) needs a positive boss_atk")
    if defn.get("boss_atk") is not None and not _pos_int(defn["boss_atk"]):
        errors.append("boss_atk must be a positive int")
    cls_err = _class_error(defn.get("boss_class"), "boss_class")
    if cls_err:
        errors.append(cls_err)
    for k in ("total_hp", "battle_hp", "duration_hours"):
        if not _pos_int(defn.get(k)):
            errors.append(f"{k} must be a positive int")
    if _pos_int(defn.get("battle_hp")) and _pos_int(defn.get("total_hp")) and defn["battle_hp"] > defn["total_hp"]:
        errors.append("battle_hp must be <= total_hp")
    bs = defn.get("boss_scale", 1)
    if not isinstance(bs, (int, float)) or isinstance(bs, bool) or bs <= 0:
        errors.append("boss_scale must be a positive number")

    phases = defn.get("phases", [])
    if not isinstance(phases, list):
        errors.append("phases must be a list")
    else:
        for i, ph in enumerate(phases):
            loc = f"phase[{i}]"
            if not isinstance(ph, dict):
                errors.append(f"{loc}: must be an object")
                continue
            t = ph.get("threshold")
            if not isinstance(t, (int, float)) or isinstance(t, bool) or not (0 < t <= 1):
                errors.append(f"{loc}: threshold must be a number in (0, 1]")
            if not (isinstance(ph.get("name"), str) and ph["name"].strip()):
                errors.append(f"{loc}: name is required")
            am = ph.get("atk_mult", 1)
            if not isinstance(am, (int, float)) or isinstance(am, bool) or am <= 0:
                errors.append(f"{loc}: atk_mult must be a positive number")
            ss = ph.get("sprite_scale", 1)
            if not isinstance(ss, (int, float)) or isinstance(ss, bool) or ss <= 0:
                errors.append(f"{loc}: sprite_scale must be a positive number")
            if "battle_hp" in ph and not _pos_int(ph["battle_hp"]):
                errors.append(f"{loc}: battle_hp must be a positive int")
            ph_cls_err = _class_error(ph.get("class"), "class")
            if ph_cls_err:
                errors.append(f"{loc}: {ph_cls_err}")
            kit = ph.get("kit")
            if kit is not None:
                k = dict(kit)
                k.setdefault("id", defn.get("boss_servant_id") or 0)  # phase kits inherit the boss id
                errors.extend(f"{loc}: {e}" for e in validate_kit(k))

    rewards = defn.get("rewards", {})
    if not isinstance(rewards, dict):
        errors.append("rewards must be an object")
    elif isinstance(rewards.get("ranks", []), list):
        for i, tier in enumerate(rewards.get("ranks", [])):
            if not isinstance(tier, dict) or not _pos_int(tier.get("top")):
                errors.append(f"rewards.ranks[{i}]: 'top' must be a positive int")
    else:
        errors.append("rewards.ranks must be a list")
    return errors
