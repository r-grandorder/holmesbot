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
# Hand-authored summonable units (event NPCs promoted to summonable, or truly custom units).
# Curated by hand like npc_servants.json; see custom_servants.example.json for the schema.
DEFAULT_CUSTOM_PATH = Path(__file__).resolve().parent / "custom_servants.json"


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
    gender: str = ""         # Atlas gender (male/female/unknown); drives a guess hint
    attribute: str = ""      # Atlas attribute (sky/earth/human/star/beast); category filter
    npc: bool = False        # hand-curated enemy/boss; art game only (npc_servants.json)
    jp: bool = False         # JP-only servant; included only via the *jp game commands
    aliases: tuple[str, ...] = ()  # extra accepted answers (NPCs + JP), normalized at match
    traits: frozenset[str] = frozenset()  # Atlas trait names; category filter
    skills: tuple[tuple[int, str, str], ...] = ()  # (slot num, name, icon URL); guess_skill
    summon_line: str | None = None  # Atlas 'firstGet' voice line text; shown on /summon
    custom: bool = False       # hand-authored summonable unit (custom_servants.json); summon-only,
    #                            excluded from the guessing games. Own rare summon tier.
    summon_weight: float = 1.0  # a custom unit's relative summon odds (ignored for normal servants)
    wishable: bool = False     # a custom unit may be /wish-ed (only consulted when custom is True;
    #                            normal servants are wishable by rule, see is_wishable)

    def assets(self, kind: str) -> dict[str, str]:
        return self.figure if kind == "figure" else self.art


def class_display(class_name: str) -> str:
    """Human-facing class label. Atlas gives Beast-class bosses internal class names like
    'unBeastOlgaMarie' or 'beastEresh'; collapse anything Beast-flavored to plain 'Beast'.
    Everything else is title-cased as before."""
    if not class_name:
        return ""
    if "beast" in class_name.lower():
        return "Beast"
    return class_name.title()


# Curated display-name overrides for servants Atlas ships under an ambiguous/colliding name
# (two "Ereshkigal"s; base vs red-haired "Super" Aoko, both of which Atlas calls "Aozaki Aoko").
# Keyed by Atlas servant id and applied at load, so they survive a data resync; the original
# Atlas name is retained as an accepted alias so guess-game answers still match either name.
NAME_OVERRIDES = {
    3300200: "Space Ereshkigal",   # Beast-class; Atlas name "Ereshkigal" collides with the Lancer
    2501500: "Super Aozaki Aoko",  # red-haired form; Atlas name "Aozaki Aoko" collides with base Aoko
}


@dataclass(frozen=True)
class ServantFilter:
    """Optional pool narrowing from the /guess category params. Each dimension is a set
    of accepted values (any-of within a dimension; dimensions AND together). A single
    chosen value is just a one-element set; /guessrandom may set several so a shown pool
    (e.g. "Saber/Archer", "4-star/5-star") doesn't give away the exact class/rarity."""

    class_names: frozenset[str] = frozenset()  # lowercased Atlas classNames
    rarities: frozenset[int] = frozenset()
    attributes: frozenset[str] = frozenset()   # lowercased Atlas attributes
    traits: frozenset[str] = frozenset()       # Atlas trait names

    @property
    def active(self) -> bool:
        return bool(self.class_names or self.rarities or self.attributes or self.traits)

    def matches(self, s: "Servant") -> bool:
        if self.class_names and s.class_name.lower() not in self.class_names:
            return False
        if self.rarities and s.rarity not in self.rarities:
            return False
        if self.attributes and s.attribute.lower() not in self.attributes:
            return False
        if self.traits and self.traits.isdisjoint(s.traits):
            return False
        return True


class ServantIndex:
    def __init__(self, servants: Iterable[Servant]) -> None:
        self._by_id: dict[int, Servant] = {s.id: s for s in servants}
        # Lazy caches keyed by include_jp: an EN round (include_jp False) excludes
        # JP-only servants so a JP name typed mid-round is ignored, not flagged.
        self._name_sets: dict[bool, frozenset[str]] = {}
        self._spaced_name_sets: dict[bool, tuple[str, ...]] = {}
        self._jp_ids: frozenset[int] | None = None

    @classmethod
    def load(
        cls,
        path: Path | str = DEFAULT_INDEX_PATH,
        npc_path: Path | str = DEFAULT_NPC_PATH,
        jp_path: Path | str = DEFAULT_JP_PATH,
        custom_path: Path | str = DEFAULT_CUSTOM_PATH,
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
        # Hand-authored summonable units: summon-only flexes (event NPCs or truly custom
        # characters). Loaded last; a custom entry's id wins over any earlier dup.
        custom_p = Path(custom_path)
        if custom_p.exists():
            for it in json.loads(custom_p.read_text()):
                s = cls._from_item(it, custom=True)
                servants = [x for x in servants if x.id != s.id]
                servants.append(s)
        return cls(s for s in servants if s.art or s.figure)

    @staticmethod
    def _from_item(
        item: dict, *, npc: bool = False, jp: bool = False, custom: bool = False
    ) -> Servant:
        override = NAME_OVERRIDES.get(item["id"])
        aliases = tuple(item.get("aliases", ()))
        if override and item["name"] not in aliases:
            aliases += (item["name"],)  # keep the original Atlas name matchable in guess games
        return Servant(
            id=item["id"],
            name=override or item["name"],
            class_name=item.get("className", ""),
            rarity=item.get("rarity", 0),
            art={str(k): v for k, v in item.get("art", {}).items() if v},
            figure={str(k): v for k, v in item.get("figure", {}).items() if v},
            face=item.get("face"),
            cv=item.get("cv"),
            gender=item.get("gender", ""),
            attribute=item.get("attribute", ""),
            npc=npc or bool(item.get("npc")),
            jp=jp or bool(item.get("jp")),
            custom=custom or bool(item.get("custom")),
            summon_weight=float(item.get("summon_weight", 1.0)),
            wishable=bool(item.get("wishable", False)),
            aliases=aliases,
            traits=frozenset(item.get("traits", ())),
            skills=tuple(
                (sk["num"], sk["name"], sk["icon"])
                for sk in item.get("skills", ())
                if sk.get("num") and sk.get("name") and sk.get("icon")
            ),
            summon_line=item.get("summon_line"),
        )

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, servant_id: int) -> Servant | None:
        return self._by_id.get(servant_id)

    def jp_ids(self) -> frozenset[int]:
        """Ids of JP-only servants, for excluding their aliases on EN rounds."""
        if self._jp_ids is None:
            self._jp_ids = frozenset(s.id for s in self._by_id.values() if s.jp)
        return self._jp_ids

    def search(self, query: str, limit: int = 25) -> list[Servant]:
        """Servants whose name contains the query (for admin autocomplete)."""
        servants = list(self._by_id.values())
        q = query.strip().lower()
        if not q:
            return servants[:limit]
        return [s for s in servants if q in s.name.lower()][:limit]

    def resembles_servant(
        self,
        text: str,
        extra: "frozenset[str] | tuple[str, ...]" = (),
        *,
        include_jp: bool = True,
    ) -> bool:
        """True if `text`, ignoring case/whitespace/punctuation, is exactly a
        servant name or a curated alias. No fuzzy matching: the chat game reacts
        to a wrong message only when it's a real-but-wrong name, so ordinary
        chatter is ignored. `extra` is the alias pool (same normalization), which
        also covers romanization variants -- e.g. 'Artoria' vs Atlas's 'Altria'.
        With include_jp False, JP-only servant names don't count (an EN round won't
        react to a JP-only guess). (Accepting the *correct* answer stays lenient; see
        matching.is_correct_guess.)"""
        from data import matching

        norm = matching.normalize(text)
        if not norm:
            return False
        names = self._name_sets.get(include_jp)
        if names is None:
            names = frozenset(
                matching.normalize(s.name)
                for s in self._by_id.values()
                if include_jp or not s.jp
            )
            self._name_sets[include_jp] = names
        return norm in names or norm in extra

    def spaced_names(self, include_jp: bool = True) -> tuple[str, ...]:
        """All servant names normalized with spaces kept, for the token-subset
        uniqueness check in matching.is_correct_guess (so 'altria' won't win a
        specific Altria variant). include_jp False drops JP-only servants from the
        corpus for EN rounds."""
        cache = self._spaced_name_sets.get(include_jp)
        if cache is None:
            from data import matching

            cache = tuple(
                matching.normalize(s.name, keep_spaces=True)
                for s in self._by_id.values()
                if include_jp or not s.jp
            )
            self._spaced_name_sets[include_jp] = cache
        return cache

    def pick(
        self,
        *,
        asset: str = "art",
        allow: Callable[[int, str], bool] | None = None,
        include_jp: bool = False,
        filt: "ServantFilter | None" = None,
        need_skills: bool = False,
    ) -> tuple[Servant, str] | None:
        """Pick a random (servant, ascension_key) for the given asset kind whose art
        passes `allow`. JP-only servants are excluded unless include_jp; `filt` narrows
        the pool further (class/rarity/attribute/trait). `need_skills` keeps only
        servants with a full 3-skill kit (guess_skill), which also drops NPC bosses."""
        gate = allow or (lambda _sid, _asc: True)
        servants = [
            s
            for s in self._by_id.values()
            if (include_jp or not s.jp)
            and not s.custom  # custom units are summon-only, never a guess target
            and (filt is None or filt.matches(s))
            and (not need_skills or len(s.skills) >= 3)
        ]
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
        filt: "ServantFilter | None" = None,
    ) -> "tuple[Servant, str | None] | None":
        """Pick any servant for the voice game: the audio challenge ignores the art
        restriction (a restricted servant can still be guessed by ear). Returns a
        NON-restricted ascension for the safe reveal art, or None if every ascension
        is restricted (the reveal then shows no image)."""
        gate = allow or (lambda _sid, _asc: True)
        # NPC/boss units are art-game only (no voice lines); JP-only servants appear
        # only via /guessvoicejp (include_jp); `filt` narrows by category.
        servants = [
            s
            for s in self._by_id.values()
            if s.art
            and not s.npc
            and not s.custom  # custom units are summon-only, never a guess target
            and (include_jp or not s.jp)
            and (filt is None or filt.matches(s))
        ]
        if not servants:
            return None
        servant = random.choice(servants)
        safe = [k for k in servant.art if gate(servant.id, k)]
        return servant, (random.choice(safe) if safe else None)

    def count_matching(
        self, filt: "ServantFilter | None", include_jp: bool = False
    ) -> int:
        """How many art-bearing servants match `filt` in the given pool. Used by
        /guessrandom to avoid rolling a filter combo with no servants."""
        return sum(
            1
            for s in self._by_id.values()
            if s.art
            and not s.custom  # custom units are summon-only, never a guess target
            and (include_jp or not s.jp)
            and (filt is None or filt.matches(s))
        )
