"""Craft Essences for the /guessce game. A CraftEssence deliberately duck-types the Servant
fields the guessing framework touches (name, id, rarity, aliases, class_name, gender, art),
so ChatRound / launch_round / reveals treat CEs exactly like servants with no changes. CEs
have no class or gender, so those are blank and the hint sequence collapses to just rarity."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CE_PATH = Path(__file__).resolve().parent / "ce.json"


@dataclass(frozen=True)
class CraftEssence:
    id: int
    name: str
    rarity: int
    art: dict[str, str]           # {"0": illustration URL}; one image per CE
    face: "str | None" = None
    cv: "str | None" = None       # CEs have no CV; present so the reveal's `if s.cv` stays safe
    aliases: tuple[str, ...] = ()
    class_name: str = ""          # CEs have no class -> class hints are skipped
    gender: str = ""              # CEs have no gender -> gender hints are skipped
    # Harmless duck-type padding so any shared code that peeks at these never AttributeErrors:
    npc: bool = False
    jp: bool = False
    custom: bool = False
    figure: dict[str, str] = field(default_factory=dict)
    skills: tuple = ()

    def assets(self, kind: str) -> dict[str, str]:
        return self.art


class CeIndex:
    """The Craft Essence pool for /guessce -- a minimal servant-index-shaped API (get / search
    / pick) so the guessing framework can use it interchangeably with the servant index."""

    def __init__(self, ces) -> None:
        self._by_id: dict[int, CraftEssence] = {c.id: c for c in ces}

    @classmethod
    def load(cls, path: "Path | str" = DEFAULT_CE_PATH) -> "CeIndex":
        p = Path(path)
        if not p.exists():
            return cls([])
        return cls(
            CraftEssence(
                id=it["id"],
                name=it["name"],
                rarity=it.get("rarity", 0),
                art={str(k): v for k, v in (it.get("art") or {}).items() if v},
                face=it.get("face"),
                aliases=tuple(it.get("aliases", ())),
            )
            for it in json.loads(p.read_text())
        )

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, ce_id: int) -> "CraftEssence | None":
        return self._by_id.get(ce_id)

    def search(self, query: str, limit: int = 25) -> "list[CraftEssence]":
        q = query.strip().lower()
        ces = list(self._by_id.values())
        if not q:
            return ces[:limit]
        return [c for c in ces if q in c.name.lower()][:limit]

    def pick(self, *, allow=None, **_ignored):
        """A random (ce, art_key) whose art passes `allow`. Signature-compatible with the
        servant index's pick(): extra kwargs (include_jp, filt, need_skills) are accepted and
        ignored, since CEs have no JP split, category filters, or skills."""
        gate = allow or (lambda _id, _k: True)
        ces = list(self._by_id.values())
        random.shuffle(ces)
        for ce in ces:
            keys = [k for k in ce.art if gate(ce.id, k)]
            if keys:
                return ce, random.choice(keys)
        return None
