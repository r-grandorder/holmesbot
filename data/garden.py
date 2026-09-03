"""The garden: heights, growth maths and flavour for /water and /mulch.

Ported in spirit from the legacy bot's /water. Deliberately dropped from that version: skill
trees, water reserves and weather. What remains is the bit that was actually fun -- everyone
has a height, watering someone grows them, and the numbers get absurd over time.

Two independent cooldowns, which is the crux of the design:
  * GROWTH_COOLDOWN_HOURS sits on the person being WATERED. Watering someone who grew
    recently still counts and still pays the waterer, it just doesn't grow them.
  * The QP payout cooldown sits on the WATERER and comes from config (WATER_QP_COOLDOWN_HOURS),
    so a player earns at a fixed rate no matter how many people they water.
"""
from __future__ import annotations

import random

# How long a plant must rest between growth spurts. On the TARGET.
GROWTH_COOLDOWN_HOURS = 1.0

STARTING_HEIGHT_MM = 1.0

# Growth scales with how tall you already are, so the numbers snowball into comedy. Each entry
# is (upper bound in mm, min growth, max growth); the last tier catches everything above.
GROWTH_TIERS = (
    (10.0, 0.1, 1.0),          # under 1cm: a sprout
    (100.0, 0.5, 3.0),         # under 10cm
    (1_000.0, 1.0, 10.0),      # under 1m
    (10_000.0, 5.0, 50.0),     # under 10m
    (float("inf"), 10.0, 100.0),  # giants
)

# --- Quality Fertilizer (bought in Da Vinci's Workshop, applied to someone else via /mulch) ---
MULCH_MULTIPLIER = 2.0     # growth multiplier while it's active
MULCH_HOURS = 6.0          # how long one application lasts
MULCH_NAME = "Quality Fertilizer"


def growth_amount(height_mm: float) -> float:
    """A random growth roll for a plant of this height, before any mulch multiplier."""
    for ceiling, low, high in GROWTH_TIERS:
        if height_mm < ceiling:
            return random.uniform(low, high)
    return random.uniform(*GROWTH_TIERS[-1][1:])


def format_height(height_mm: float) -> str:
    """Height in whichever unit reads best, so a sprout is '3.40mm' and a monster is '1.20km'."""
    if height_mm < 10:
        return f"{height_mm:.2f}mm"
    if height_mm < 1_000:
        return f"{height_mm / 10:.2f}cm"
    if height_mm < 1_000_000:
        return f"{height_mm / 1_000:.2f}m"
    return f"{height_mm / 1_000_000:.2f}km"


# --- flavour -------------------------------------------------------------------------------
# No emojis in copy, per the project convention. {name} is the watered player's display name.

GREW_LINES = (
    "{name} stretches toward the light.",
    "{name} drinks deeply and stands a little taller.",
    "{name} unfurls another leaf.",
    "{name} shoots up like a beanstalk.",
    "{name} is positively thriving.",
    "{name}'s roots drink it all in.",
    "{name} creaks upward, visibly pleased.",
    "{name} sprouts with enthusiasm.",
)

NO_GROWTH_LINES = (
    "{name} is already well watered, and politely declines seconds.",
    "{name} appreciates the gesture but needs time before growing again.",
    "{name} rustles contentedly. Nothing more happens.",
    "{name} is still photosynthesizing the last batch.",
    "{name} has had quite enough water for now.",
    "{name} absorbs it gratefully, but does not grow.",
)

# Watering the bot. Holmes is a consulting detective being treated as shrubbery: he deduces
# what is going on, disapproves, and grows regardless. Never at the player's expense.
HOLMES_LINES = (
    "I am not a houseplant. ... Though I note the watering can was aimed with some precision.",
    "Curious. You have concluded I require watering. I cannot fault the reasoning; only the premise.",
    "This is undignified. Continue.",
    "Watering a detective. I have catalogued stranger methods of investigation, but not many.",
    "You water me expecting growth. I water you with deduction. Neither of us learns.",
    "I shall record this in my notes under 'unsolicited horticulture'.",
    "A remarkable experiment. The subject is unamused and, regrettably, taller.",
    "I would object, but the evidence suggests it is working.",
)


def water_line(display_name: str, grew: bool) -> str:
    pool = GREW_LINES if grew else NO_GROWTH_LINES
    return random.choice(pool).format(name=display_name)


def holmes_line() -> str:
    return random.choice(HOLMES_LINES)
