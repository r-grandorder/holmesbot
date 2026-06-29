"""Shared branding: the QP currency emote and formatting helper."""
from __future__ import annotations

# The QP emote is per-application (a custom emote's ID belongs to one bot app), so
# it comes from config (the QP_EMOTE env var) and is set once at startup via
# configure(). Falls back to plain text "QP" if unset, which renders fine.
_QP_EMOTE = "QP"


def configure(emote: str) -> None:
    """Set the QP emote from config. Called once at startup, before cogs load."""
    global _QP_EMOTE
    _QP_EMOTE = emote or "QP"


def qp_emote() -> str:
    """The configured QP emote string (a custom-emote ref, or the fallback text)."""
    return _QP_EMOTE


def qp(amount: int) -> str:
    """Format an amount as QP, e.g. '1,234 <:qp:...>'."""
    return f"{amount:,} {_QP_EMOTE}"


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
