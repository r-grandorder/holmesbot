"""Shared branding: the QP currency emote and formatting helper."""
from __future__ import annotations

# The bot's application emote for QP. Custom emoji render by ID, so the name part
# is cosmetic. If it ever shows as raw text, confirm the emote is static (a
# leading "a:" is needed for animated emotes).
QP_EMOTE = "<:qp:1519844883408355358>"


def qp(amount: int) -> str:
    """Format an amount as QP, e.g. '1,234 <:qp:...>'."""
    return f"{amount:,} {QP_EMOTE}"


MAX_QP = 100_000_000_000  # 100 billion: per-user cap, enforced in the scoring service

_SUFFIXES = {"k": 10**3, "m": 10**6, "b": 10**9, "t": 10**12, "q": 10**15}


def parse_qp(text: str) -> int | None:
    """Parse an amount like '1k', '3.2b', '500m', or '1,000' to an int (None if invalid)."""
    s = text.strip().lower().replace(",", "")
    if not s:
        return None
    mult = 1
    if s[-1] in _SUFFIXES:
        mult, s = _SUFFIXES[s[-1]], s[:-1]
    try:
        value = float(s) * mult
    except ValueError:
        return None
    return int(value) if value >= 0 else None
