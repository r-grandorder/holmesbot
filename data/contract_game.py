"""Pure game math for the contracted-servant feature: progression curves, Power, and the
weighted summon roll. No discord/DB dependencies, so it's unit-testable in isolation.
All numbers here are tuning knobs -- adjust to the server's real activity."""
from __future__ import annotations

import random

# --- progression ---
BASE_CAP = 60          # same for every servant; grails raise it
GRAIL_STEP = 5         # cap += 5 per grail applied, uncapped
XP_PER_MSG = 25        # xp granted per (cooldown-gated) chat message
XP_COOLDOWN = 60.0     # seconds between xp-earning messages, per user (enforced in the cog)
LEVELUP_MILESTONE_EVERY = 10   # "milestones" announce mode pings only on multiples of this (and at cap)

# --- Power (single stat) = BASE_POWER[rarity] * (1 + level*POWER_PER_LEVEL) ---
BASE_POWER = {0: 500, 1: 800, 2: 1200, 3: 2000, 4: 3500, 5: 5000}
NPC_POWER = 6000       # NPC bosses sit above 5-stars
POWER_PER_LEVEL = 0.05

# --- summon odds: pick a rarity tier by weight, then a uniform servant within it ---
TIER_WEIGHTS = {5: 1.0, 4: 5.0, 3: 40.0, 2: 25.0, 1: 20.0, 0: 8.8}
NPC_WEIGHT = 0.2       # the NPC-boss tier (ultra-rare flex)

# Notable servants that should be a RARE, exciting pull regardless of their star rating,
# rather than tier-fillers -- Angra is the sole 0-star (would otherwise be ~8.8%), Habetrot
# a specific 4-star (would otherwise be ~1-in-3000). Pulled out of their normal rarity tier
# into this dedicated one; split uniformly among however many are present. Extend freely.
SPECIAL_SERVANTS = {1100100, 404200}   # Angra Mainyu, Habetrot
SPECIAL_WEIGHT = 2.0

# A player's /wish target gets its own personal tier at this weight (~1% of a roll). NPC
# bosses are exempt -- they can't be wished, staying an un-buyable rare flex.
WISH_WEIGHT = 1.0

# --- grail events: two flavored random drops (random host each), independently tunable ---
# Single grail: the first to claim takes exactly ONE grail, then it self-deletes.
GRAIL_SINGLE_COOLDOWN = 40 * 60   # seconds; at most one single drop per guild per window
GRAIL_SINGLE_CHANCE = 0.02        # chance per qualifying message once off cooldown
# Grail present box: USES people each grab one grail until it's empty, then it self-deletes.
GRAIL_BOX_COOLDOWN = 90 * 60
GRAIL_BOX_CHANCE = 0.01
GRAIL_BOX_USES_MIN, GRAIL_BOX_USES_MAX = 3, 8   # how many one-grail claims the box holds
GRAIL_EVENT_TTL = 120.0           # seconds an unfinished event lingers before self-deleting

# --- pity: guarantee a (random) 5-star by this many rolls without one ---
# 100 = ~3x more generous than FGO's spark distance (FGO: 900 SQ / 30 SQ per multi = 300
# rolls). Natural pulls stay the majority, but pity is actually reachable here (~25k QP), so
# it works as a real safety net. Ours grants a RANDOM 5-star, not a pick like FGO's spark.
PITY_5STAR = 100


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


def display_art(servant, allow=None) -> "str | None":
    """The servant's highest-ascension art URL that passes the restriction gate (the coolest
    safe reveal), or None if it has no art or every ascension is restricted. `allow` is the
    content-policy predicate allow(servant_id, ascension_key)."""
    art = getattr(servant, "art", None)
    if not art:
        return None
    gate = allow or (lambda _sid, _k: True)
    keys = sorted(
        (k for k in art if gate(servant.id, k)),
        key=lambda k: int(k) if str(k).isdigit() else -1,
    )
    return art[keys[-1]] if keys else None


def resets_pity(servant) -> bool:
    """A 5-star (NPC bosses are also rarity 5) ends a pity streak."""
    return servant.rarity == 5


def is_wishable(servant) -> bool:
    """Whether a servant may be set as a /wish: a summonable, non-NPC servant (NPC bosses
    are exempt)."""
    return bool(servant) and not servant.jp and bool(servant.art) and not servant.npc


def roll_servant(index, *, force_5star: bool = False, wish: "int | None" = None, allow=None):
    """Weighted FGO-like roll from the NA + NPC pool (exclude JP-only). With force_5star,
    return a random 5-star (the pity guarantee, never the wished one). `wish` is a servant
    id the roller is chasing: a summonable non-NPC gets its own personal tier at
    WISH_WEIGHT (~1%). `allow(servant_id, ascension_key)` is the content-policy gate:
    servants with no allowed art are excluded entirely (fail-safe), so a fully-restricted
    servant is never summoned. Returns a Servant."""
    gate = allow or (lambda _sid, _k: True)

    def _has_safe_art(s) -> bool:
        return any(gate(s.id, k) for k in s.art)

    pool = [s for s in index._by_id.values() if not s.jp and s.art and _has_safe_art(s)]
    npcs = [s for s in pool if s.npc]
    wished = index.get(wish) if wish is not None else None
    if not (wished is not None and is_wishable(wished) and _has_safe_art(wished)):
        wished = None
    wid = wished.id if wished is not None else None
    special = [s for s in pool if s.id in SPECIAL_SERVANTS and not s.npc and s.id != wid]
    by_rarity: dict[int, list] = {}
    for s in pool:
        if not s.npc and s.id not in SPECIAL_SERVANTS and s.id != wid:
            by_rarity.setdefault(s.rarity, []).append(s)
    if force_5star and by_rarity.get(5):
        return random.choice(by_rarity[5])
    tiers: list = []
    weights: list[float] = []
    if wished is not None:
        tiers.append("wish")
        weights.append(WISH_WEIGHT)
    if npcs:
        tiers.append("npc")
        weights.append(NPC_WEIGHT)
    if special:
        tiers.append("special")
        weights.append(SPECIAL_WEIGHT)
    for rarity, weight in TIER_WEIGHTS.items():
        if by_rarity.get(rarity):
            tiers.append(rarity)
            weights.append(weight)
    tier = random.choices(tiers, weights=weights, k=1)[0]
    if tier == "wish":
        return wished
    if tier == "npc":
        return random.choice(npcs)
    if tier == "special":
        return random.choice(special)
    return random.choice(by_rarity[tier])


# --- duels ---
DUEL_REWARD = 30          # QP to the winner
DUEL_DAILY_CAP = 5        # reward-earning duels per player per day, ALL opponents combined
DUEL_COOLDOWN = 20        # seconds between a challenger's duels (anti-flood)
DUEL_PAIR_COOLDOWN = 180  # seconds before the same two players can duel again (anti-targeting)
CLASS_ADVANTAGE = 1.5     # effective-power multiplier when your class beats the opponent's

# The two clean FGO triangles (attacker class -> the class it beats). Extra classes
# (Berserker, Ruler, Avenger, Moon Cancer, Alter Ego, Foreigner, Pretender, Beast) stay
# neutral here and lean on Power; their real matchups can drop in later without touching duels.
_CLASS_BEATS = {
    "saber": "lancer", "lancer": "archer", "archer": "saber",
    "rider": "caster", "caster": "assassin", "assassin": "rider",
}


def class_multiplier(attacker_class: str, defender_class: str) -> float:
    """The attacker's effective-power multiplier vs the defender under the class triangle."""
    beats = _CLASS_BEATS.get((attacker_class or "").lower())
    return CLASS_ADVANTAGE if beats == (defender_class or "").lower() else 1.0


def duel_odds(power_a: int, class_a: str, power_b: int, class_b: str) -> float:
    """P(A wins) = A's effective-power share, where a class advantage multiplies Power. Higher
    Power/level and a class edge both tilt the odds, but upsets stay possible."""
    eff_a = power_a * class_multiplier(class_a, class_b)
    eff_b = power_b * class_multiplier(class_b, class_a)
    total = eff_a + eff_b
    return 0.5 if total <= 0 else eff_a / total
