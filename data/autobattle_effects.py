"""Autobattle buff/debuff system. Ported from the legacy autochess effects module, unchanged
except the item import (now data.autobattle_items). Operates on dict-based servant battle state
(active_buffs / active_debuffs / current_hp / max_hp), the shape the resolver builds.
"""
from __future__ import annotations

import random
from enum import Enum
from typing import Optional


class EffectType(Enum):
    """Effect types for buffs and debuffs."""

    # Buffs
    ATTACK_UP = "attack_up"
    DEFENSE_UP = "defense_up"
    EVADE = "evade"
    ANTI_PURGE = "anti_purge"
    HEAL = "heal"
    CLEANSE = "cleanse"
    HEAL_ON_DAMAGE = "heal_on_damage"
    ATK_UP_ON_DAMAGE = "atk_up_on_damage"
    ATK_UP_ON_HURT = "atk_up_on_hurt"
    ATK_UP_ON_KILL = "atk_up_on_kill"
    DEF_UP_ON_KILL = "def_up_on_kill"
    GUTS = "guts"
    INSTANT_KILL = "instant_kill"
    ORDER_CHANGE = "order_change"
    IGNORE_EVADE = "ignore_evade"
    PIERCING = "piercing"
    PASS_BUFFS = "pass_buffs"
    HEALING_PER_TURN = "healing_per_turn"
    MAX_HP_UP = "max_hp_up"
    GUTS_PIERCE = "guts_pierce"
    CURSE_IMMUNITY = "curse_immunity"
    SKILL_SEAL_RESIST = "skill_seal_resist"

    # Debuffs
    STUN = "stun"
    SLEEP = "sleep"
    SKILL_SEAL = "skill_seal"
    POISON = "poison"
    CURSE = "curse"
    BURN = "burn"
    DEFENSE_DOWN = "defense_down"
    ATTACK_DOWN = "attack_down"
    SACRIFICE = "sacrifice"
    BUFF_REMOVAL = "buff_removal"


BUFF_TYPES = {
    EffectType.ATTACK_UP,
    EffectType.DEFENSE_UP,
    EffectType.EVADE,
    EffectType.ANTI_PURGE,
    EffectType.HEAL,
    EffectType.CLEANSE,
    EffectType.HEAL_ON_DAMAGE,
    EffectType.ATK_UP_ON_DAMAGE,
    EffectType.ATK_UP_ON_HURT,
    EffectType.ATK_UP_ON_KILL,
    EffectType.DEF_UP_ON_KILL,
    EffectType.GUTS,
    EffectType.INSTANT_KILL,
    EffectType.ORDER_CHANGE,
    EffectType.IGNORE_EVADE,
    EffectType.PIERCING,
    EffectType.PASS_BUFFS,
    EffectType.HEALING_PER_TURN,
    EffectType.MAX_HP_UP,
    EffectType.GUTS_PIERCE,
    EffectType.CURSE_IMMUNITY,
    EffectType.SKILL_SEAL_RESIST,
}

DEBUFF_TYPES = {
    EffectType.STUN,
    EffectType.SLEEP,
    EffectType.SKILL_SEAL,
    EffectType.POISON,
    EffectType.CURSE,
    EffectType.BURN,
    EffectType.DEFENSE_DOWN,
    EffectType.ATTACK_DOWN,
    EffectType.SACRIFICE,
    EffectType.BUFF_REMOVAL,
}

# Effect types that stack additively (stored as lists).
STACKABLE_EFFECT_TYPES = {
    EffectType.ATTACK_UP,
    EffectType.ATTACK_DOWN,
    EffectType.DEFENSE_UP,
    EffectType.DEFENSE_DOWN,
    EffectType.BURN,
    EffectType.CURSE,
    EffectType.POISON,
    EffectType.INSTANT_KILL,
}


class Effect:
    """A buff or debuff on a servant during battle."""

    def __init__(
        self,
        effect_type: EffectType,
        value: float = 0.0,
        duration: int = 1,
        source: str = "unknown",
        buff_duration: int = 3,
        unremovable: bool = False,
    ):
        self.type = effect_type
        self.value = value
        self.duration = duration  # -1 permanent, 0 expired
        self.source = source
        self.buff_duration = buff_duration
        self.consumed = False  # one-time effects (guts)
        self.unremovable = unremovable

    def is_buff(self) -> bool:
        return self.type in BUFF_TYPES

    def is_debuff(self) -> bool:
        return self.type in DEBUFF_TYPES

    def is_permanent(self) -> bool:
        return self.duration == -1

    def is_expired(self) -> bool:
        return self.duration == 0

    def decrement_duration(self):
        if self.duration > 0:
            self.duration -= 1

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "value": self.value,
            "duration": self.duration,
            "source": self.source,
            "buff_duration": self.buff_duration,
            "consumed": self.consumed,
            "unremovable": self.unremovable,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Effect":
        effect = cls(
            effect_type=EffectType(data["type"]),
            value=data["value"],
            duration=data["duration"],
            source=data["source"],
            buff_duration=data.get("buff_duration", 3),
            unremovable=data.get("unremovable", False),
        )
        effect.consumed = data.get("consumed", False)
        return effect

    def __repr__(self) -> str:
        dur = "inf" if self.is_permanent() else str(self.duration)
        return f"Effect({self.type.value}, value={self.value}, duration={dur})"


def apply_effect(servant: dict, effect: Effect, battle_log: "list[str] | None" = None) -> bool:
    """Apply an effect to a servant. Returns False if blocked (item cleanse / resist)."""
    # Items that cleanse stun/sleep on application.
    if effect.type in (EffectType.STUN, EffectType.SLEEP):
        from data.autobattle_items import get_item

        item = get_item(servant.get("equipped_item"))
        if item and item.get("trigger") == "on_debuff":
            if (item.get("effect", {}) or {}).get("type") == "CLEANSE_STUN_SLEEP":
                if battle_log is not None:
                    battle_log.append(
                        f"{item.get('emoji', '')} {servant['name']} shrugs off {effect.type.value}."
                    )
                servant["equipped_item"] = None
                return False

    if effect.type == EffectType.SKILL_SEAL:
        resist = get_effect(servant, EffectType.SKILL_SEAL_RESIST)
        if resist and random.random() < resist.value:
            if battle_log is not None:
                battle_log.append(f"{servant['name']} resists skill seal.")
            return False
        from data.autobattle_items import get_item

        item = get_item(servant.get("equipped_item"))
        if item and item.get("trigger") == "on_debuff":
            if (item.get("effect", {}) or {}).get("type") == "CLEANSE_SKILL_SEAL":
                if battle_log is not None:
                    battle_log.append(f"{item.get('emoji', '')} {servant['name']} negates skill seal.")
                servant["equipped_item"] = None
                return False

    effect_dict = effect.to_dict()

    if effect.is_buff():
        if effect.type in STACKABLE_EFFECT_TYPES:
            servant["active_buffs"].setdefault(effect.type.value, []).append(effect_dict)
        else:
            servant["active_buffs"][effect.type.value] = effect_dict

        if effect.type == EffectType.HEAL:
            if servant["current_hp"] >= servant["max_hp"]:
                return True
            servant["current_hp"] = min(servant["current_hp"] + int(effect.value), servant["max_hp"])
            return True

        if effect.type == EffectType.CLEANSE:
            kept = {}
            for dtype, ddata in servant["active_debuffs"].items():
                if dtype == "sacrifice":
                    kept[dtype] = ddata
                    continue
                if isinstance(ddata, list):
                    keep = [d for d in ddata if Effect.from_dict(d).unremovable]
                    if keep:
                        kept[dtype] = keep
                elif Effect.from_dict(ddata).unremovable:
                    kept[dtype] = ddata
            servant["active_debuffs"] = kept
            return True

        if effect.type == EffectType.MAX_HP_UP:
            inc = int(servant["max_hp"] * effect.value) if effect.value <= 10.0 else int(effect.value)
            servant["max_hp"] += inc
            servant["current_hp"] += inc

    elif effect.is_debuff():
        if effect.type == EffectType.BUFF_REMOVAL:
            from data.autobattle_items import get_item

            item = get_item(servant.get("equipped_item"))
            if item and item.get("trigger") == "on_debuff":
                if (item.get("effect", {}) or {}).get("type") == "CLEANSE_BUFF_REMOVAL":
                    if battle_log is not None:
                        battle_log.append(
                            f"{item.get('emoji', '')} {servant['name']} is protected from buff removal."
                        )
                    servant["equipped_item"] = None
                    return False

            kept_buffs = {}
            for btype, bdata in servant["active_buffs"].items():
                if isinstance(bdata, list):
                    keep = [b for b in bdata if Effect.from_dict(b).unremovable]
                    if keep:
                        kept_buffs[btype] = keep
                elif Effect.from_dict(bdata).unremovable:
                    kept_buffs[btype] = bdata
            kept_debuffs = {}
            for dtype, ddata in servant["active_debuffs"].items():
                if dtype == "sacrifice":
                    kept_debuffs[dtype] = ddata
                    continue
                if isinstance(ddata, list):
                    keep = [d for d in ddata if Effect.from_dict(d).unremovable]
                    if keep:
                        kept_debuffs[dtype] = keep
                elif Effect.from_dict(ddata).unremovable:
                    kept_debuffs[dtype] = ddata
            servant["active_buffs"] = kept_buffs
            servant["active_debuffs"] = kept_debuffs
            return True

        if effect.type in STACKABLE_EFFECT_TYPES:
            servant["active_debuffs"].setdefault(effect.type.value, []).append(effect_dict)
        else:
            servant["active_debuffs"][effect.type.value] = effect_dict

    return True


def remove_effect(servant: dict, effect_type: EffectType):
    key = effect_type.value
    servant["active_buffs"].pop(key, None)
    servant["active_debuffs"].pop(key, None)


def get_effect(servant: dict, effect_type: EffectType) -> Optional[Effect]:
    """Active effect (buff or debuff). Stackable types return a synthetic summed Effect."""
    key = effect_type.value
    for bucket in ("active_buffs", "active_debuffs"):
        if key in servant[bucket]:
            data = servant[bucket][key]
            if effect_type in STACKABLE_EFFECT_TYPES:
                if not data:
                    return None
                total = sum(Effect.from_dict(e).value for e in data)
                longest = max(Effect.from_dict(e).duration for e in data)
                return Effect(effect_type, value=total, duration=longest)
            return Effect.from_dict(data)
    return None


def has_effect(servant: dict, effect_type: EffectType) -> bool:
    key = effect_type.value
    if effect_type in STACKABLE_EFFECT_TYPES:
        return bool(servant["active_buffs"].get(key)) or bool(servant["active_debuffs"].get(key))
    return key in servant["active_buffs"] or key in servant["active_debuffs"]


def process_effect_durations(servant: dict):
    """Tick down all effect durations by one turn; drop expired ones."""
    for bucket in ("active_buffs", "active_debuffs"):
        expired = []
        for key, data in servant[bucket].items():
            etype = EffectType(key)
            if etype in STACKABLE_EFFECT_TYPES:
                kept = []
                for single in data:
                    eff = Effect.from_dict(single)
                    eff.decrement_duration()
                    if not eff.is_expired():
                        kept.append(eff.to_dict())
                if kept:
                    servant[bucket][key] = kept
                else:
                    expired.append(key)
            else:
                eff = Effect.from_dict(data)
                eff.decrement_duration()
                if eff.is_expired():
                    expired.append(key)
                    if eff.type == EffectType.MAX_HP_UP:
                        dec = (
                            int(servant["max_hp"] * eff.value / (1.0 + eff.value))
                            if eff.value <= 10.0
                            else int(eff.value)
                        )
                        servant["max_hp"] -= dec
                        servant["current_hp"] = min(servant["current_hp"], servant["max_hp"])
                else:
                    servant[bucket][key] = eff.to_dict()
        for key in expired:
            del servant[bucket][key]
