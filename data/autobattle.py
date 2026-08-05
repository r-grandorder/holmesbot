"""Autobattle stat model (the engine will join it here later).

Servants level UNCAPPED in this bot, so instead of FGO's per-servant growth curves we keep each
servant's Atlas atk/hp *profile* (atkBase/atkMax, hpBase/hpMax) and apply one shared linear
curve, extrapolated unbounded past a reference level. Deterministic -- no per-hit variance.
"""
from __future__ import annotations

import json
from pathlib import Path

# Bot level at which a servant reaches its Atlas max stats; above it, stats keep scaling linearly.
STAT_REF_LEVEL = 120

# Fallback max (atk, hp) by rarity for units with no Atlas profile -- custom units and NPCs --
# so they land in the same ballpark as real servants. Tunable.
_FALLBACK_MAX = {
    5: (11000, 15000),
    4: (9000, 12000),
    3: (7500, 10000),
    2: (6500, 9000),
    1: (5500, 8000),
    0: (9500, 13000),
}


def _scale(base: float, mx: float, level: int) -> int:
    """Linear base->max across levels 1..STAT_REF_LEVEL, extrapolated unbounded above it."""
    factor = (level - 1) / (STAT_REF_LEVEL - 1)
    return max(1, round(base + (mx - base) * factor))


def battle_stats(servant, level: int) -> "tuple[int, int]":
    """(atk, hp) for a servant at an (uncapped) level. Real servants use their Atlas atk/hp
    profile; custom units / NPCs (no profile) fall back to a rarity table. Deterministic, so a
    given (servant, level) always yields the same stats -- easy to reason about and tune."""
    level = max(1, level)
    atk_max = getattr(servant, "atk_max", 0) or 0
    hp_max = getattr(servant, "hp_max", 0) or 0
    if atk_max > 0 and hp_max > 0:
        return _scale(servant.atk_base, atk_max, level), _scale(servant.hp_base, hp_max, level)
    fa, fh = _FALLBACK_MAX.get(getattr(servant, "rarity", 0), _FALLBACK_MAX[3])
    return _scale(fa * 0.1, fa, level), _scale(fh * 0.1, fh, level)


# --- class advantage (ported from the legacy autochess; FGO triangles at 1.2x, Berserker 1.1x,
#     the Foreigner<>Berserker and U-Olga Marie special affinities). Disadvantage is the inverse.
_CLASS_ADVANTAGES = {
    ("saber", "lancer"): 1.2, ("lancer", "archer"): 1.2, ("archer", "saber"): 1.2,
    ("rider", "caster"): 1.2, ("caster", "assassin"): 1.2, ("assassin", "rider"): 1.2,
    ("ruler", "mooncancer"): 1.2, ("mooncancer", "avenger"): 1.2, ("avenger", "ruler"): 1.2,
    ("alterego", "foreigner"): 1.2, ("foreigner", "pretender"): 1.2, ("pretender", "alterego"): 1.2,
    ("berserker", "saber"): 1.1, ("berserker", "archer"): 1.1, ("berserker", "lancer"): 1.1,
    ("berserker", "rider"): 1.1, ("berserker", "caster"): 1.1, ("berserker", "assassin"): 1.1,
    ("berserker", "berserker"): 1.1, ("berserker", "ruler"): 1.1, ("berserker", "avenger"): 1.1,
    ("berserker", "alterego"): 1.1, ("berserker", "mooncancer"): 1.1, ("berserker", "pretender"): 1.1,
    ("saber", "berserker"): 1.1, ("archer", "berserker"): 1.1, ("lancer", "berserker"): 1.1,
    ("rider", "berserker"): 1.1, ("caster", "berserker"): 1.1, ("assassin", "berserker"): 1.1,
    ("ruler", "berserker"): 1.1, ("avenger", "berserker"): 1.1, ("alterego", "berserker"): 1.1,
    ("mooncancer", "berserker"): 1.1, ("pretender", "berserker"): 1.1,
    ("berserker", "foreigner"): 0.83, ("foreigner", "berserker"): 1.2,
    ("alterego", "rider"): 1.1, ("alterego", "caster"): 1.1, ("alterego", "assassin"): 1.1,
    ("pretender", "saber"): 1.1, ("pretender", "archer"): 1.1, ("pretender", "lancer"): 1.1,
    ("unbeastolgamarie", "mooncancer"): 1.2, ("unbeastolgamarie", "foreigner"): 1.2,
    ("unbeastolgamarie", "berserker"): 1.1, ("unbeastolgamarie", "avenger"): 0.8,
    ("unbeastolgamarie", "saber"): 1.0, ("unbeastolgamarie", "archer"): 1.0,
    ("unbeastolgamarie", "lancer"): 1.0, ("unbeastolgamarie", "rider"): 1.0,
    ("unbeastolgamarie", "caster"): 1.0, ("unbeastolgamarie", "assassin"): 1.0,
    ("avenger", "unbeastolgamarie"): 1.2, ("foreigner", "unbeastolgamarie"): 1.2,
    ("berserker", "unbeastolgamarie"): 1.1,
    ("saber", "unbeastolgamarie"): 0.8, ("archer", "unbeastolgamarie"): 0.8,
    ("lancer", "unbeastolgamarie"): 0.8, ("rider", "unbeastolgamarie"): 0.8,
    ("caster", "unbeastolgamarie"): 0.8, ("assassin", "unbeastolgamarie"): 0.8,
    ("mooncancer", "unbeastolgamarie"): 0.8,
}


def class_advantage(attacker_class: str, defender_class: str) -> float:
    """Damage multiplier from the class matchup. 1.0 neutral; Shielder always takes neutral."""
    atk = (attacker_class or "").lower()
    dfn = (defender_class or "").lower()
    if dfn == "shielder":
        return 1.0
    if (atk, dfn) in _CLASS_ADVANTAGES:
        return _CLASS_ADVANTAGES[(atk, dfn)]
    reverse = _CLASS_ADVANTAGES.get((dfn, atk))
    return 1.0 / reverse if reverse else 1.0


# --- PvE stages (ported from the legacy autochess encounters; preconfigured enemy teams over an
#     Atlas background, keyed by stage id and grouped by difficulty). Winning grants no reward for
#     now (XP is chat-only) -- these are boss challenges. Lives in data/autobattle_encounters.json.
_ENCOUNTERS_PATH = Path(__file__).parent / "autobattle_encounters.json"
_encounters_cache: "dict | None" = None


def load_encounters() -> dict:
    """The PvE stage table: {stage_id: {name, difficulty, description, bg_image, servants}}."""
    global _encounters_cache
    if _encounters_cache is None:
        try:
            with open(_ENCOUNTERS_PATH, encoding="utf-8") as f:
                _encounters_cache = json.load(f)
        except FileNotFoundError:
            _encounters_cache = {}
    return _encounters_cache
