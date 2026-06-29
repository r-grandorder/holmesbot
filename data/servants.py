from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_INDEX_PATH = Path(__file__).resolve().parent / "servants.json"


@dataclass(frozen=True)
class Servant:
    id: int
    name: str
    class_name: str
    rarity: int
    art: dict[str, str]     # charaGraph ascension -> URL (guess_servant, reveals)
    figure: dict[str, str]  # charaFigure ascension -> URL (guess_shadow)
    face: str | None = None  # Atlas face portrait (host avatars)
    cv: str | None = None    # seiyuu / voice actor (Atlas profile.cv)

    def assets(self, kind: str) -> dict[str, str]:
        return self.figure if kind == "figure" else self.art


class ServantIndex:
    def __init__(self, servants: Iterable[Servant]) -> None:
        self._by_id: dict[int, Servant] = {s.id: s for s in servants}
        self._norm_name_set: frozenset[str] | None = None  # lazy cache for resembles_servant
        self._spaced_names: tuple[str, ...] | None = None  # lazy cache for uniqueness checks

    @classmethod
    def load(cls, path: Path | str = DEFAULT_INDEX_PATH) -> "ServantIndex":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"Servant index not found at {p}. "
                "Run `python scripts/sync_atlas.py` to build it."
            )
        raw = json.loads(p.read_text())
        servants = (
            Servant(
                id=item["id"],
                name=item["name"],
                class_name=item.get("className", ""),
                rarity=item.get("rarity", 0),
                art={str(k): v for k, v in item.get("art", {}).items() if v},
                figure={str(k): v for k, v in item.get("figure", {}).items() if v},
                face=item.get("face"),
                cv=item.get("cv"),
            )
            for item in raw
        )
        return cls(s for s in servants if s.art or s.figure)

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, servant_id: int) -> Servant | None:
        return self._by_id.get(servant_id)

    def search(self, query: str, limit: int = 25) -> list[Servant]:
        """Servants whose name contains the query (for admin autocomplete)."""
        servants = list(self._by_id.values())
        q = query.strip().lower()
        if not q:
            return servants[:limit]
        return [s for s in servants if q in s.name.lower()][:limit]

    def resembles_servant(
        self, text: str, extra: "frozenset[str] | tuple[str, ...]" = ()
    ) -> bool:
        """True if `text`, ignoring case/whitespace/punctuation, is exactly a
        servant name or a curated alias. No fuzzy matching: the chat game reacts
        to a wrong message only when it's a real-but-wrong name, so ordinary
        chatter is ignored. `extra` is the alias pool (same normalization), which
        also covers romanization variants -- e.g. 'Artoria' vs Atlas's 'Altria'.
        (Accepting the *correct* answer stays lenient; see matching.is_correct_guess.)"""
        from data import matching

        norm = matching.normalize(text)
        if not norm:
            return False
        if self._norm_name_set is None:
            self._norm_name_set = frozenset(
                matching.normalize(s.name) for s in self._by_id.values()
            )
        return norm in self._norm_name_set or norm in extra

    def spaced_names(self) -> tuple[str, ...]:
        """All servant names normalized with spaces kept, for the token-subset
        uniqueness check in matching.is_correct_guess (so 'altria' won't win a
        specific Altria variant)."""
        if self._spaced_names is None:
            from data import matching

            self._spaced_names = tuple(
                matching.normalize(s.name, keep_spaces=True) for s in self._by_id.values()
            )
        return self._spaced_names

    def pick(
        self, *, asset: str = "art", allow: Callable[[int, str], bool] | None = None
    ) -> tuple[Servant, str] | None:
        """Pick a random (servant, ascension_key) for the given asset kind whose
        art passes `allow`. Returns None if nothing is eligible."""
        gate = allow or (lambda _sid, _asc: True)
        servants = list(self._by_id.values())
        random.shuffle(servants)
        for servant in servants:
            keys = [k for k in servant.assets(asset) if gate(servant.id, k)]
            if keys:
                return servant, random.choice(keys)
        return None

    def pick_for_voice(
        self, allow: Callable[[int, str], bool] | None = None
    ) -> "tuple[Servant, str | None] | None":
        """Pick any servant for the voice game: the audio challenge ignores the art
        restriction (a restricted servant can still be guessed by ear). Returns a
        NON-restricted ascension for the safe reveal art, or None if every ascension
        is restricted (the reveal then shows no image)."""
        gate = allow or (lambda _sid, _asc: True)
        servants = [s for s in self._by_id.values() if s.art]
        if not servants:
            return None
        servant = random.choice(servants)
        safe = [k for k in servant.art if gate(servant.id, k)]
        return servant, (random.choice(safe) if safe else None)
