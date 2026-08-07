"""Autobattle kits: each servant's passive ability for the (ported) autochess engine.

Authoring flow mirrors requirements.in -> requirements.txt: the source of truth is one JSON
file per servant under data/kits/<id>_<slug>.json (small, easy to diff, no merge conflicts);
`make kits` validates them and compiles a single data/kits.json (gitignored, baked at Docker
build like servants.json). This module holds the schema + the compiled-file loader only --
battle execution lives in the autobattle engine, not here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_KITS_PATH = Path(__file__).resolve().parent / "kits.json"

# A kit is a one-shot passive that fires on its trigger during a battle.
TRIGGERS = frozenset({"battle_start", "on_enter", "on_defeat", "on_kill"})

TARGETS = frozenset(
    {
        "self",
        "party",
        "party_others",
        "enemy",
        "all_enemies",
        "random_ally",
        "random_enemy",
        "next_ally",
    }
)

# The full effect vocabulary (the legacy EffectType enum). The compiler rejects anything else,
# so a typo in a hand-authored kit fails the build instead of silently breaking a battle.
EFFECT_TYPES = frozenset(
    {
        "attack_up",
        "defense_up",
        "evade",
        "anti_purge",
        "heal",
        "cleanse",
        "heal_on_damage",
        "atk_up_on_damage",
        "atk_up_on_hurt",
        "atk_up_on_kill",
        "def_up_on_kill",
        "guts",
        "instant_kill",
        "order_change",
        "ignore_evade",
        "piercing",
        "pass_buffs",
        "healing_per_turn",
        "max_hp_up",
        "guts_pierce",
        "curse_immunity",
        "skill_seal_resist",
        "stun",
        "sleep",
        "skill_seal",
        "poison",
        "curse",
        "burn",
        "defense_down",
        "attack_down",
        "sacrifice",
        "buff_removal",
    }
)


@dataclass(frozen=True)
class SkillEffect:
    effect_type: str
    value: float
    duration: int
    target: str
    unremovable: bool = False
    buff_duration: "int | None" = None  # legacy optional field, preserved for the engine

    @classmethod
    def from_dict(cls, d: dict) -> "SkillEffect":
        return cls(
            effect_type=d["effect_type"],
            value=d["value"],
            duration=d["duration"],
            target=d["target"],
            unremovable=d.get("unremovable", False),
            buff_duration=d.get("buff_duration"),
        )

    def to_dict(self) -> dict:
        d = {"effect_type": self.effect_type, "value": self.value,
             "duration": self.duration, "target": self.target}
        if self.unremovable:
            d["unremovable"] = True
        if self.buff_duration is not None:
            d["buff_duration"] = self.buff_duration
        return d


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    trigger: str
    effects: tuple[SkillEffect, ...]
    servant_name: str = ""  # informational, for authoring + the battle log
    class_name: str = ""
    cooldown: int = 0
    max_uses: int = -1

    @classmethod
    def from_dict(cls, d: dict) -> "Skill":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            trigger=d["trigger"],
            effects=tuple(SkillEffect.from_dict(e) for e in d["effects"]),
            servant_name=d.get("servant_name", ""),
            class_name=d.get("class_name", ""),
            cooldown=d.get("cooldown", 0),
            max_uses=d.get("max_uses", -1),
        )

    def to_dict(self) -> dict:
        d = {"name": self.name, "description": self.description,
             "trigger": self.trigger, "effects": [e.to_dict() for e in self.effects]}
        if self.servant_name:
            d["servant_name"] = self.servant_name
        if self.class_name:
            d["class_name"] = self.class_name
        if self.cooldown:
            d["cooldown"] = self.cooldown
        if self.max_uses != -1:
            d["max_uses"] = self.max_uses
        return d


def validate_kit(kit: dict) -> "list[str]":
    """Schema-check one kit dict; returns error strings (empty = valid). Shared with the web kit
    editor so it rejects exactly what the build compiler (scripts/build_kits.py) would."""
    errors: list[str] = []
    for k in ("id", "name", "trigger", "effects"):
        if k not in kit:
            errors.append(f"missing field: {k}")
    if errors:
        return errors
    if not isinstance(kit["id"], int) or isinstance(kit["id"], bool):
        errors.append("id must be an int")
    if not isinstance(kit["name"], str) or not kit["name"].strip():
        errors.append("name must be a non-empty string")
    if kit["trigger"] not in TRIGGERS:
        errors.append(f"bad trigger: {kit['trigger']!r}")
    if not isinstance(kit["effects"], list) or not kit["effects"]:
        errors.append("effects must be a non-empty list")
    else:
        for i, e in enumerate(kit["effects"]):
            loc = f"effect[{i}]"
            if not isinstance(e, dict):
                errors.append(f"{loc}: must be an object")
                continue
            if e.get("effect_type") not in EFFECT_TYPES:
                errors.append(f"{loc}: bad effect_type {e.get('effect_type')!r}")
            if e.get("target") not in TARGETS:
                errors.append(f"{loc}: bad target {e.get('target')!r}")
            if not isinstance(e.get("value"), (int, float)) or isinstance(e.get("value"), bool):
                errors.append(f"{loc}: value must be a number")
            if not isinstance(e.get("duration"), int) or isinstance(e.get("duration"), bool):
                errors.append(f"{loc}: duration must be an int")
    return errors


class KitIndex:
    """servant_id -> Skill, loaded from the compiled kits.json. Empty if the file is missing
    (so a deploy without kits baked degrades to vanilla attackers rather than crashing)."""

    def __init__(self, kits: "dict[int, Skill]") -> None:
        self._by_id = dict(kits)

    @classmethod
    def load(cls, path: "Path | str" = DEFAULT_KITS_PATH) -> "KitIndex":
        p = Path(path)
        if not p.exists():
            return cls({})
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls({int(sid): Skill.from_dict(sk) for sid, sk in raw.items()})

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, servant_id: int) -> "Skill | None":
        return self._by_id.get(servant_id)

    def items(self) -> "list[tuple[int, Skill]]":
        """(servant_id, Skill) for every kitted servant -- used by the /ab kit lookup."""
        return list(self._by_id.items())

    def set(self, servant_id: int, skill: "Skill") -> None:
        """Layer one (validated) kit onto the live index; applies to the next battle."""
        self._by_id[servant_id] = skill

    def reset(self, kits: "dict[int, Skill]") -> None:
        """Replace all kits -- used to re-apply overrides over a fresh baked load."""
        self._by_id = dict(kits)
