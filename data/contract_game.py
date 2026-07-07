"""Pure game math for the contracted-servant feature: progression curves, Power, and the
weighted summon roll. No discord/DB dependencies, so it's unit-testable in isolation.
All numbers here are tuning knobs -- adjust to the server's real activity."""
from __future__ import annotations

import random

# --- progression ---
BASE_CAP = 60          # same for every servant; grails raise it
GRAIL_STEP = 5         # cap += 5 per grail applied, uncapped
XP_PER_MSG = 15        # xp granted per (cooldown-gated) chat message
XP_COOLDOWN = 60.0     # seconds between xp-earning messages, per user (enforced in the cog)

# --- Power (single stat) = BASE_POWER[rarity] * (1 + level*POWER_PER_LEVEL) ---
BASE_POWER = {0: 500, 1: 800, 2: 1200, 3: 2000, 4: 3500, 5: 5000}
NPC_POWER = 6000       # NPC bosses sit above 5-stars
POWER_PER_LEVEL = 0.05

# --- summon odds: pick a rarity tier by weight, then a uniform servant within it ---
TIER_WEIGHTS = {5: 1.0, 4: 5.0, 3: 40.0, 2: 25.0, 1: 20.0, 0: 8.8}
NPC_WEIGHT = 0.2       # the NPC-boss tier (ultra-rare flex)

# --- grail drops ---
GRAIL_DROP_COOLDOWN = 45 * 60  # seconds; at most one drop per guild per this window
GRAIL_DROP_CHANCE = 0.05       # chance per qualifying message once off cooldown
GRAIL_MIN, GRAIL_MAX = 1, 5
CLAIM_TTL = 60.0               # seconds a drop stays claimable before self-deleting


def level_cap(grails_used: int) -> int:
    return BASE_CAP + grails_used * GRAIL_STEP


def xp_to_next(level: int) -> int:
    """XP required to go from `level` to level+1 (a gentle upward curve)."""
    return 200 + 4 * level


def apply_xp(level: int, xp: int, cap: int) -> tuple[int, int]:
    """Roll accumulated xp into levels, stopping at `cap`. Returns (new_level, leftover_xp);
    xp is discarded once the cap is reached (grail the cap up to keep leveling)."""
    while level < cap and xp >= xp_to_next(level):
        xp -= xp_to_next(level)
        level += 1
    if level >= cap:
        xp = 0
    return level, xp


def power(servant, level: int) -> int:
    base = NPC_POWER if getattr(servant, "npc", False) else BASE_POWER.get(servant.rarity, 500)
    return round(base * (1 + level * POWER_PER_LEVEL))


def display_art(servant) -> "str | None":
    """The servant's highest-ascension art URL (the coolest reveal), or None."""
    art = getattr(servant, "art", None)
    if not art:
        return None
    keys = sorted(art.keys(), key=lambda k: int(k) if str(k).isdigit() else -1)
    return art[keys[-1]]


def roll_servant(index):
    """Weighted FGO-like roll from the NA + NPC pool (exclude JP-only). Returns a Servant."""
    pool = [s for s in index._by_id.values() if not s.jp and s.art]
    npcs = [s for s in pool if s.npc]
    by_rarity: dict[int, list] = {}
    for s in pool:
        if not s.npc:
            by_rarity.setdefault(s.rarity, []).append(s)
    tiers: list = []
    weights: list[float] = []
    if npcs:
        tiers.append("npc")
        weights.append(NPC_WEIGHT)
    for rarity, weight in TIER_WEIGHTS.items():
        if by_rarity.get(rarity):
            tiers.append(rarity)
            weights.append(weight)
    tier = random.choices(tiers, weights=weights, k=1)[0]
    bucket = npcs if tier == "npc" else by_rarity[tier]
    return random.choice(bucket)
