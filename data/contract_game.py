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

# A player's /wish is their pity SPARK target: when the pity guarantee fires it delivers the
# wished servant (else a random 5-star). Wishing does NOT boost natural pull odds. NPC bosses
# can't be wished (see is_wishable), staying un-buyable rare flexes.

# --- grail events: two flavored random drops (random host each), independently tunable ---
# Single grail: the first to claim takes exactly ONE grail, then it self-deletes.
GRAIL_SINGLE_COOLDOWN = 40 * 60   # seconds; at most one single drop per guild per window
GRAIL_SINGLE_CHANCE = 0.02        # chance per qualifying message once off cooldown
# Grail present box: USES people each grab one grail until it's empty, then it self-deletes.
GRAIL_BOX_COOLDOWN = 90 * 60
GRAIL_BOX_CHANCE = 0.01
GRAIL_BOX_USES_MIN, GRAIL_BOX_USES_MAX = 3, 8   # how many one-grail claims the box holds
GRAIL_EVENT_TTL = 120.0           # seconds an unfinished event lingers before self-deleting

# --- QP reward event (Bunyan's qp_reward: a chatter randomly finds QP, auto-awarded). The
# amount comes from the HOST's wealth tier (a triangular roll), so a rich host like Gilgamesh
# (up to ~10 summons) pays far more than a poor one like Jinako. Self-deletes. ---
QP_REWARD_COOLDOWN = 40 * 60      # seconds; at most one QP drop per guild per window
QP_REWARD_CHANCE = 0.02           # chance per qualifying message once off cooldown
QP_REWARD_TTL = 10               # seconds the notification lingers before self-deleting

# --- pity: guarantee a (random) 5-star every this many rolls; natural 5-stars carry over ---
# 100 = ~3x more generous than FGO's spark distance (FGO: 900 SQ / 30 SQ per multi = 300
# rolls). Natural pulls stay the majority, but pity is actually reachable here (~25k QP), so
# it works as a real safety net. Ours grants a RANDOM 5-star, not a pick like FGO's spark.
PITY_5STAR = 100

# Summon Ticket (/redeem): after the wish check, the chance to pull from the ticket's RARE
# pool (NPC bosses + custom units) instead of a plain 5-star -- far above their base summon
# odds, so a ticket is a real shot at the flexes. (The wish chance is config-driven.)
SUMMON_TICKET_RARE_CHANCE = 0.1


def level_cap(grails_used: int) -> int:
    return BASE_CAP + grails_used * GRAIL_STEP


def xp_to_next(level: int) -> int:
    """XP to go from `level` to level+1. Flat and cheap below BASE_CAP so the 1-60 climb stays
    quick (~4 messages a level); only past the base cap (grail territory) does the cost ramp,
    making post-cap levels the real grind."""
    if level < BASE_CAP:
        return 100
    return 100 + 25 * (level - BASE_CAP + 1)


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


def ticket_roll(index, wish: "int | None" = None, *, chance: float = 0.15,
                rare_chance: float = SUMMON_TICKET_RARE_CHANCE, allow=None):
    """A Summon Ticket pull, in priority order: (1) with probability `chance`, the wished
    servant (if a valid, summonable one is set); (2) else with probability `rare_chance`, a
    random NPC boss or custom unit -- the ticket's elevated shot at the rare flexes, far above
    their base summon odds; (3) else a random 5-star. Returns (servant, is_wish); is_wish is
    True only for outcome (1)."""
    gate = allow or (lambda _sid, _k: True)

    def _safe(s) -> bool:
        return any(gate(s.id, k) for k in s.art)

    wished = index.get(wish) if wish is not None else None
    if wished is not None and is_wishable(wished) and _safe(wished) and random.random() < chance:
        return wished, True
    pool = [s for s in index._by_id.values() if not s.jp and s.art and _safe(s)]
    rare = [s for s in pool if s.npc or s.custom]
    if rare and random.random() < rare_chance:
        return random.choice(rare), False
    fives = [s for s in pool if not s.npc and not s.custom and s.rarity == 5]
    return (random.choice(fives) if fives else wished), False


def qp_reward_amount(tier: "tuple[int, int, int]") -> int:
    """A random QP-drop amount for a host's wealth tier (min, mode, max): a triangular roll
    peaking at the mode, so the payout tracks how rich the host is."""
    lo, mode, hi = tier
    return round(random.triangular(lo, hi, mode))


def underdog_factor(winner_size: int, avg_size: float, cap: "float | None" = None) -> float:
    """War-reward multiplier for winning while outnumbered: avg_size / winner_size, clamped to
    [1.0, cap] (cap defaults to WAR_UNDERDOG_CAP). 1.0 for an average-or-larger winner (no
    bonus); up to cap for a heavy upset."""
    if cap is None:
        cap = WAR_UNDERDOG_CAP
    if winner_size <= 0 or avg_size <= 0:
        return 1.0
    return min(cap, max(1.0, avg_size / winner_size))


def war_ticket_reward(factor: float) -> int:
    """Summon Tickets for a war win, scaled by the underdog factor from WAR_WIN_TICKETS (an
    even/expected win) up to WAR_UPSET_TICKETS (a max upset at WAR_UNDERDOG_CAP)."""
    span = WAR_UNDERDOG_CAP - 1.0
    frac = 0.0 if span <= 0 else max(0.0, min(1.0, (factor - 1.0) / span))
    return round(WAR_WIN_TICKETS + frac * (WAR_UPSET_TICKETS - WAR_WIN_TICKETS))


def is_wishable(servant) -> bool:
    """Whether a servant may be set as a /wish: a summonable, non-NPC servant (NPC bosses are
    exempt). A custom unit is wishable only when its own `wishable` flag is set."""
    return (
        bool(servant)
        and not servant.jp
        and bool(servant.art)
        and not servant.npc
        and (not servant.custom or servant.wishable)
    )


def _summon_buckets(pool):
    """Weighted summon buckets from `pool`, as a list of (label, weight, members): a roll picks
    a bucket by weight then a uniform member. Shared by roll_servant and summon_rates so the
    displayed odds always match real pulls. Also returns by_rarity (for the pity path). Wishing
    does not change these odds -- the wish is a pity spark target, not a rate boost."""
    npcs = [s for s in pool if s.npc]
    special = [s for s in pool if s.id in SPECIAL_SERVANTS and not s.npc]
    customs = [s for s in pool if s.custom]  # each is its own weighted tier
    by_rarity: dict[int, list] = {}
    for s in pool:
        if not s.npc and not s.custom and s.id not in SPECIAL_SERVANTS:
            by_rarity.setdefault(s.rarity, []).append(s)
    buckets: list = []
    if npcs:
        buckets.append(("NPC bosses", NPC_WEIGHT, npcs))
    if special:
        buckets.append(("Special (Angra/Habetrot)", SPECIAL_WEIGHT, special))
    for rarity, weight in TIER_WEIGHTS.items():
        if by_rarity.get(rarity):
            buckets.append((f"{rarity}-star", weight, by_rarity[rarity]))
    for s in customs:  # each custom unit competes on its own per-unit summon weight
        buckets.append((s.name, s.summon_weight, [s]))
    return buckets, by_rarity


def roll_servant(index, *, force_5star: bool = False, wish: "int | None" = None, allow=None):
    """Weighted FGO-like roll from the NA + NPC pool (exclude JP-only). With force_5star (the
    pity guarantee), return the roller's wished servant if they have a valid one set (their
    spark), otherwise a random 5-star. `wish` is a servant id; it matters ONLY on the guarantee
    and does not boost natural pull odds. `allow(servant_id, ascension_key)` is the
    content-policy gate: servants with no allowed art are excluded (fail-safe). Returns a
    Servant (or None if the pool is empty)."""
    gate = allow or (lambda _sid, _k: True)

    def _has_safe_art(s) -> bool:
        return any(gate(s.id, k) for k in s.art)

    pool = [s for s in index._by_id.values() if not s.jp and s.art and _has_safe_art(s)]
    buckets, by_rarity = _summon_buckets(pool)
    if force_5star:  # pity guarantee: the wished servant (spark), else a random 5-star
        wished = index.get(wish) if wish is not None else None
        if wished is not None and is_wishable(wished) and _has_safe_art(wished):
            return wished
        return random.choice(by_rarity[5]) if by_rarity.get(5) else None
    if not buckets:
        return None
    members = random.choices(
        [m for _, _, m in buckets], weights=[w for _, w, _ in buckets], k=1
    )[0]
    return random.choice(members)


def summon_rates(index, *, allow=None):
    """The live summon rate table (for /summonodds), as (rows, total_weight). Each row is
    (kind, label, weight, pct, count, per_each_pct) with kind in {'tier','custom'}. Uses the
    same buckets as roll_servant minus the per-user wish tier, so the numbers match real pulls;
    per_each_pct is one member's chance (pct / count)."""
    gate = allow or (lambda _sid, _k: True)
    pool = [
        s for s in index._by_id.values()
        if not s.jp and s.art and any(gate(s.id, k) for k in s.art)
    ]
    buckets, _ = _summon_buckets(pool)
    total = sum(w for _, w, _ in buckets) or 1.0
    rows = []
    for label, weight, members in buckets:
        pct = 100.0 * weight / total
        count = len(members)
        kind = "custom" if members and members[0].custom else "tier"
        rows.append((kind, label, weight, pct, count, pct / count))
    return rows, total


# --- duels ---
DUEL_REWARD = 30          # QP to the winner
DUEL_DAILY_CAP = 5        # reward-earning duels per player per day, ALL opponents combined
DUEL_COOLDOWN = 20        # seconds between a challenger's duels (anti-flood)
DUEL_PAIR_COOLDOWN = 180  # seconds before the same two players can duel again (anti-targeting)
CLASS_ADVANTAGE = 1.5     # effective-power multiplier when your class beats the opponent's

# --- switching the active contract to an already-owned servant ---
SWITCH_COST = 50           # QP to switch (re-activate a servant you already contracted)
SWITCH_COOLDOWN = 30 * 60  # seconds between switches per user (also blocks duel counter-picking)

# --- faction war ---
WAR_REWARD = 5000         # base QP to each member of the winning faction when a season ends
WAR_UNDERDOG_CAP = 2.0    # max QP-reward multiplier for winning outnumbered (avg/size, clamped)
WAR_WIN_TICKETS = 1       # Summon Tickets for an even/expected faction win
WAR_UPSET_TICKETS = 4     # Summon Tickets for a maximum-upset win (scaled between the two)
WAR_DEFAULT_DAYS = 7.0    # default season length before a war auto-ends (mod can override)

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
