from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_KEEP = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def normalize(text: str, *, keep_spaces: bool = False) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    base = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    base = _WS.sub(" ", _KEEP.sub(" ", base)).strip()
    return base if keep_spaces else base.replace(" ", "")


def is_correct_guess(
    guess: str,
    answer: str,
    *,
    aliases: "frozenset[str] | tuple[str, ...]" = (),
    all_names: "tuple[str, ...]" = (),
    threshold: int = 85,
) -> bool:
    g = normalize(guess)
    a = normalize(answer)
    if not g:
        return False
    if g == a:  # exact name -- always accepted
        return True
    if g in aliases:  # DB-curated accepted aliases (already normalized)
        return True
    # A partial (token-subset) guess, e.g. "artoria" for "Artoria Pendragon" -- but
    # only when it uniquely identifies one servant. Otherwise a shared name like
    # "martha" or "altria" would win every variant; the fuller name is required.
    sg = normalize(guess, keep_spaces=True)
    if len(g) >= 3 and fuzz.token_set_ratio(sg, normalize(answer, keep_spaces=True)) >= 90:
        if _unique_subset(sg, all_names):
            return True
    # Typo tolerance on the full name -- but only when `answer` is the closest
    # name in the whole roster. Without this guard a typo'd variant like "altria
    # pendragon alncer" clears the threshold against several Altria variants at
    # once (they share a long stem), and would win whichever variant's round is
    # live rather than the one actually meant.
    ra = fuzz.ratio(g, a)
    if ra < threshold:
        return False
    if not all_names:
        return True
    best_other = max(
        (fuzz.ratio(g, n.replace(" ", "")) for n in all_names if n.replace(" ", "") != a),
        default=0.0,
    )
    return ra > best_other


def _unique_subset(spaced_guess: str, all_names: "tuple[str, ...]") -> bool:
    """True if `spaced_guess` token-matches at most one servant name. With no corpus
    provided (callers that don't care), assume unique."""
    if not all_names:
        return True
    hits = 0
    for name in all_names:
        if fuzz.token_set_ratio(spaced_guess, name) >= 90:
            hits += 1
            if hits > 1:
                return False
    return True
