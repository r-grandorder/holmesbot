"""Autobattle stat model (the engine will join it here later).

Servants level UNCAPPED in this bot, so instead of FGO's per-servant growth curves we keep each
servant's Atlas atk/hp *profile* (atkBase/atkMax, hpBase/hpMax) and apply one shared linear
curve, extrapolated unbounded past a reference level. Deterministic -- no per-hit variance.
"""
from __future__ import annotations

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
