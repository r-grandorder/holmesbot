from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_INDEX_PATH = Path(__file__).resolve().parent / "servants.json"
# Hand-curated NPC/boss units (the Beasts, story bosses). Maintained by hand and kept
# out of the Atlas sync, so re-running scripts/sync_atlas.py never clobbers them.
DEFAULT_NPC_PATH = Path(__file__).resolve().parent / "npc_servants.json"
# JP-only servants (Atlas JP region, English names + community nicknames), built by
# `python scripts/sync_atlas.py --jp`. Loaded into the index but gated behind the
# *jp game commands via the jp flag.
DEFAULT_JP_PATH = Path(__file__).resolve().parent / "servants_jp.json"


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
    npc: bool = False        # hand-curated enemy/boss; art game only (npc_servants.json)
    jp: bool = False         # JP-only servant; included only via the *jp game commands
    aliases: tuple[str, ...] = ()  # extra accepted answers (NPCs + JP), normalized at match

    def assets(self, kind: str) -> dict[str, str]:
        return self.figure if kind == "figure" else self.art


class ServantIndex:
    def __init__(self, servants: Iterable[Servant]) -> None:
        self._by_id: dict[int, Servant] = {s.id: s for s in servants}
        self._norm_name_set: frozenset[str] | None = None  # lazy cache for resembles_servant
        self._spaced_names: tuple[str, ...] | None = None  # lazy cache for uniqueness checks

    @classmethod
    def load(
        cls,
        path: Path | str = DEFAULT_INDEX_PATH,
        npc_path: Path | str = DEFAULT_NPC_PATH,
        jp_path: Path | str = DEFAULT_JP_PATH,
    ) -> "ServantIndex":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"Servant index not found at {p}. "
                "Run `python scripts/sync_atlas.py` to build it."
            )
        # The index merges three sources, all keyed by Atlas id so matching, reveals,
        # and restrictions treat them uniformly. Flags gate where each may appear:
        #   - playable NA servants (servants.json)            -> all games
        #   - hand-curated NPC bosses (npc)                   -> art game only
        #   - JP-only servants (jp)                           -> only the *jp commands
        servants: list[Servant] = [cls._from_item(it) for it in json.loads(p.read_text())]
        npc_p = Path(npc_path)
        if npc_p.exists():
            servants += [cls._from_item(it, npc=True) for it in json.loads(npc_p.read_text())]
        jp_p = Path(jp_path)
        if jp_p.exists():
            # NA (servants.json) is regenerated fresh at image build, but
            # servants_jp.json is the reviewed/committed file -- so a servant that
            # graduated to NA since the last JP refresh can appear in both. Let the
            # NA entry win (drop the stale JP dup) so it stays in the regular pool
            # rather than being gated behind the *jp commands.
            known = {s.id for s in servants}
            servants += [
                cls._from_item(it, jp=True)
                for it in json.loads(jp_p.read_text())
                if it["id"] not in known
            ]
        return cls(s for s in servants if s.art or s.figure)

    @staticmethod
    def _from_item(item: dict, *, npc: bool = False, jp: bool = False) -> Servant:
        return Servant(
            id=item["id"],
            name=item["name"],
            class_name=item.get("className", ""),
            rarity=item.get("rarity", 0),
            art={str(k): v for k, v in item.get("art", {}).items() if v},
            figure={str(k): v for k, v in item.get("figure", {}).items() if v},
            face=item.get("face"),
            cv=item.get("cv"),
            npc=npc or bool(item.get("npc")),
            jp=jp or bool(item.get("jp")),
            aliases=tuple(item.get("aliases", ())),
        )

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
        self,
        *,
        asset: str = "art",
        allow: Callable[[int, str], bool] | None = None,
        include_jp: bool = False,
    ) -> tuple[Servant, str] | None:
        """Pick a random (servant, ascension_key) for the given asset kind whose
        art passes `allow`. JP-only servants are excluded unless include_jp."""
        gate = allow or (lambda _sid, _asc: True)
        servants = [s for s in self._by_id.values() if include_jp or not s.jp]
        random.shuffle(servants)
        for servant in servants:
            keys = [k for k in servant.assets(asset) if gate(servant.id, k)]
            if keys:
                return servant, random.choice(keys)
        return None

    def pick_for_voice(
        self,
        allow: Callable[[int, str], bool] | None = None,
        *,
        include_jp: bool = False,
    ) -> "tuple[Servant, str | None] | None":
        """Pick any servant for the voice game: the audio challenge ignores the art
        restriction (a restricted servant can still be guessed by ear). Returns a
        NON-restricted ascension for the safe reveal art, or None if every ascension
        is restricted (the reveal then shows no image)."""
        gate = allow or (lambda _sid, _asc: True)
        # NPC/boss units are art-game only (no voice lines); JP-only servants appear
        # only via /guessvoicejp (include_jp).
        servants = [
            s
            for s in self._by_id.values()
            if s.art and not s.npc and (include_jp or not s.jp)
        ]
        if not servants:
            return None
        servant = random.choice(servants)
        safe = [k for k in servant.art if gate(servant.id, k)]
        return servant, (random.choice(safe) if safe else None)
