"""All-at-once autobattle resolver, ported from the legacy autochess battle module.

Adapted for this bot: combatants are built from our Servant + level (data.autobattle.battle_stats)
and kits (data.kits.Skill, string triggers/targets), the class triangle is data.autobattle.
class_advantage, attack variance is kept, items are supported, and the log is clean text (no
legacy custom emotes). Dropped: the streaming path, raid-boss abilities, and property buffs.

Public API:
    build_state(player_team, enemy_team)  -> battle_state   (teams: list of Combatant specs)
    resolve(battle_state, max_turns=50)   -> battle_state   (mutates + returns; sets victory/draw)
    combatant(servant, level, position, kit=None, item=None) -> dict
"""
from __future__ import annotations

import random

from data.autobattle import battle_stats, class_advantage
from data.autobattle_effects import (
    Effect,
    EffectType,
    apply_effect,
    get_effect,
    has_effect,
    process_effect_durations,
)
from data.autobattle_items import get_item

# Triggers, as strings (matching data.kits).
BATTLE_START, ON_ENTER, ON_DEFEAT, ON_KILL = "battle_start", "on_enter", "on_defeat", "on_kill"


# Custom Discord emotes, ported verbatim from the legacy bot (the r/grandorder server's emoji the
# bot renders in the log + status). If any id is wrong for this server, fix it here.
CLASS_EMOJI = {
    "saber": "<:saber:1440481539673817198>",
    "archer": "<:archer:1440481136206938214>",
    "lancer": "<:lancer:1440481416126402681>",
    "rider": "<:rider:1440481497999212614>",
    "assassin": "<:assassin:1440481168540827668>",
    "caster": "<:caster:1440481367459627111>",
    "alterego": "<:alterego:1440481092170809355>",
    "avenger": "<:avenger:1440481199717093378>",
    "ruler": "<:ruler:1440481520581083167>",
    "mooncancer": "<:mooncancer:1440481441874972743>",
    "foreigner": "<:foreigner:1440481391447117874>",
    "pretender": "<:pretender:1440481473336578130>",
    "beast": "<:beast:1440481328540815450>",
    "shielder": "<:shielder:1440481559294509086>",
    "berserker": "<:berserker:1440481348644114432>",
}
_UNKNOWN_CLASS = "<:unknown:1440481579704258671>"

# Buff/debuff emotes (keys are EffectType values). Used in the log + the team-status effect line.
EFFECT_EMOJI = {
    "attack_up": "<:AtkUp:1440521246944268308>",
    "defense_up": "<:DefUp:1440521367534567484>",
    "evade": "<:Evade:1440521385658028062>",
    "anti_purge": "<:antipurgedefense:1279519626379788419>",
    "guts": "<:Guts:1440521420818878502>",
    "instant_kill": "<:IK:1440521470060003368>",
    "ignore_evade": "<:IgnoreEvade:1440521726218862643>",
    "piercing": "<:TeamPierce:1440525339687129088>",
    "pass_buffs": "<:PassBuffOnDeath:1440523078915461221>",
    "heal": "<:Heal:1440928092993622076>",
    "cleanse": "✨",
    "healing_per_turn": "<:HPPerTurn:1440521453949943898>",
    "heal_on_damage": "<:HPAbsorb:1440521436002254979>",
    "atk_up_on_damage": "<:AtkUpOnKill:1440523055091683439>",
    "atk_up_on_hurt": "<:AtkUpOnKill:1440523055091683439>",
    "atk_up_on_kill": "<:AtkUpOnKill:1440523055091683439>",
    "def_up_on_kill": "<:DefUpOnKill:1440525315922198579>",
    "order_change": "<:OrderChange:1440521485495173150>",
    "max_hp_up": "<:MaxHPUp:1440622720244256868>",
    "stun": "<:Stun:1440521536762019991>",
    "sleep": "<:Sleep:1440521519221444708>",
    "skill_seal": "<:SkillSeal:1440523028349063238>",
    "poison": "<:Poison:1440521502792613950>",
    "curse": "<:Curse:1440521337167937657>",
    "burn": "<:Burn:1440521276040024116>",
    "defense_down": "<:DefDown:1440521352200060938>",
    "attack_down": "<:AtkDown:1440521208561926144>",
    "sacrifice": "<:IKSelf:1440614431187931178>",
    "buff_removal": "🌀",
    "curse_immunity": "🛡️",
    "skill_seal_resist": "<:SkillSeal:1440523028349063238>",
}


def class_emoji(class_name: str) -> str:
    return CLASS_EMOJI.get((class_name or "").lower(), _UNKNOWN_CLASS)


def format_name(s: dict) -> str:
    return f"{class_emoji(s.get('className', ''))} {s['name']}"


def format_effect_description(effect_type: str, value: float, duration: int) -> str:
    """Human-readable effect line for the battle log (ported from the legacy)."""
    emoji = EFFECT_EMOJI.get(effect_type, "✨")
    effect_name = effect_type.replace("_", " ").title()
    if effect_type in ("attack_up", "defense_up", "attack_down", "defense_down"):
        value_str = f"{int(value * 100)}%"
    elif effect_type in ("max_hp_up", "healing_per_turn") and value <= 10.0:
        value_str = f"{int(value * 100)}% HP"
    elif effect_type == "order_change":
        if value == -1:
            return f"{emoji} moves to back"
        if value == 1:
            return f"{emoji} moves to front"
        return f"{emoji} swaps position"
    elif effect_type in ("heal", "max_hp_up", "healing_per_turn", "heal_on_damage"):
        value_str = f"{int(value)} HP"
    elif effect_type == "guts":
        value_str = f"{int(value)} lives"
    else:
        value_str = ""
    duration_str = (
        "permanent" if duration == -1 else "1 turn" if duration == 1 else f"{duration} turns"
    )
    if value_str:
        return f"{emoji} {effect_name} ({value_str}) for {duration_str}"
    return f"{emoji} {effect_name} for {duration_str}"


def _skill_has_effect(skill, effect_type: str) -> bool:
    return bool(skill) and any(e.effect_type == effect_type for e in skill.effects)


# --- combatant / battle-state construction -----------------------------------------------------

def combatant(servant, level: int, position: int, kit=None, item: "str | None" = None) -> dict:
    """A battle-state dict for one servant, built from our data (stats + optional kit + item)."""
    atk, hp = battle_stats(servant, level)
    skill_state = (
        {"name": kit.name, "trigger": kit.trigger, "activated_this_battle": False} if kit else None
    )
    return {
        "servant_id": servant.id,
        "name": servant.name,
        "className": (servant.class_name or "").lower(),
        "level": level,
        "max_hp": hp,
        "current_hp": hp,
        "atk": atk,
        "alive": True,
        "position": position,
        "active_buffs": {},
        "active_debuffs": {},
        "skill": kit,  # a data.kits.Skill or None
        "skill_state": skill_state,
        "equipped_item": item,
    }


def build_state(player_team: list, enemy_team: list) -> dict:
    """Assemble a battle state. Each team is a list of combatant() dicts (positions 0..n)."""
    return {
        "player_servants": list(player_team),
        "enemy_servants": list(enemy_team),
        "battle_log": [],
        "current_turn": 0,
        "game_over": False,
        "victory": False,
        "draw": False,
    }


# --- kit execution ------------------------------------------------------------------------------

def _skill_targets(target: str, user: dict, user_team: list, enemy_team: list) -> list:
    if target == "self":
        return [user]
    if target == "party":
        return [s for s in user_team if s["alive"]]
    if target == "party_others":
        return [s for s in user_team if s["alive"] and s["servant_id"] != user["servant_id"]]
    if target == "enemy":
        front = get_next_alive_servant(enemy_team)
        return [front] if front else []
    if target == "all_enemies":
        return [s for s in enemy_team if s["alive"]]
    if target == "random_ally":
        allies = [s for s in user_team if s["alive"]]
        return [random.choice(allies)] if allies else []
    if target == "random_enemy":
        foes = [s for s in enemy_team if s["alive"]]
        return [random.choice(foes)] if foes else []
    if target == "next_ally":
        idx = user_team.index(user)
        for i in list(range(idx + 1, len(user_team))) + list(range(0, idx)):
            if user_team[i]["alive"]:
                return [user_team[i]]
        return []
    return []


def execute_skill(skill, user: dict, user_team: list, enemy_team: list, battle_log: list):
    """Apply a kit's effects to their targets, logging each one, and mark it used (once/battle)."""
    battle_log.append(f"✨ {user['name']} activates skill: **{skill.name}**!")
    for se in skill.effects:
        try:
            etype = EffectType(se.effect_type)
        except ValueError:
            continue
        effect = Effect(
            etype,
            value=se.value,
            duration=se.duration,
            source=skill.name,
            buff_duration=se.buff_duration if se.buff_duration is not None else 3,
            unremovable=se.unremovable,
        )
        for target in _skill_targets(se.target, user, user_team, enemy_team):
            applied = apply_effect(target, effect, battle_log)
            # Persisting effects get a "gains ..." line; instant ones (heal/cleanse/max_hp_up)
            # speak for themselves, buff_removal gets its own line. (Matches the legacy log.)
            if se.effect_type not in ("heal", "cleanse", "buff_removal", "max_hp_up"):
                desc = format_effect_description(se.effect_type, se.value, se.duration)
                battle_log.append(f"  \N{RIGHTWARDS ARROW} {target['name']} gains {desc}")
            elif se.effect_type == "buff_removal" and applied:
                battle_log.append(f"  🌀 {target['name']}'s buffs are removed!")
    if user.get("skill_state"):
        user["skill_state"]["activated_this_battle"] = True


def check_and_trigger_skills(trigger, user, user_team, enemy_team, battle_log, sealed_by_enemy=False):
    skill = user.get("skill")
    state = user.get("skill_state")
    if not skill or not state or skill.trigger != trigger:
        return
    # seal_immune (e.g. raid bosses) ignores both an applied skill-seal debuff AND the on-enter
    # "sealed_by_enemy" path (the latter never consults skill_seal_resist), so the kit always fires.
    immune = user.get("seal_immune")
    sealed = has_effect(user, EffectType.SKILL_SEAL) and not immune
    if trigger == ON_ENTER:
        if (sealed_by_enemy and not immune) or sealed:
            battle_log.append(f"{format_name(user)}'s skill is sealed!")
            return
    else:
        if state["activated_this_battle"] or sealed:
            return
    execute_skill(skill, user, user_team, enemy_team, battle_log)


def trigger_battle_start_skills(player, enemy, battle_log):
    for s in player:
        check_and_trigger_skills(BATTLE_START, s, player, enemy, battle_log)
    for s in enemy:
        check_and_trigger_skills(BATTLE_START, s, enemy, player, battle_log)


# --- combat helpers -----------------------------------------------------------------------------

def get_next_alive_servant(servants: list) -> "dict | None":
    for s in servants:
        if s["alive"]:
            return s
    return None


def process_dot_effects(servant: dict, battle_log: list) -> int:
    total = 0
    poison = get_effect(servant, EffectType.POISON)
    if poison:
        dmg = int(servant["max_hp"] * poison.value)
        servant["current_hp"] -= dmg
        total += dmg
        battle_log.append(f"{format_name(servant)} takes {dmg} poison damage.")
    curse = get_effect(servant, EffectType.CURSE)
    if curse:
        if has_effect(servant, EffectType.CURSE_IMMUNITY):
            battle_log.append(f"{format_name(servant)}'s curse immunity blocks the curse.")
        else:
            dmg = int(curse.value)
            servant["current_hp"] -= dmg
            total += dmg
            battle_log.append(f"{format_name(servant)} takes {dmg} curse damage.")
    burn = get_effect(servant, EffectType.BURN)
    if burn:
        dmg = int(burn.value)
        servant["current_hp"] -= dmg
        total += dmg
        battle_log.append(f"{format_name(servant)} takes {dmg} burn damage.")
    return total


def process_healing_effects(servant: dict, battle_log: list) -> int:
    healing = get_effect(servant, EffectType.HEALING_PER_TURN)
    if not healing or servant["current_hp"] >= servant["max_hp"]:
        return 0
    amount = int(servant["max_hp"] * healing.value) if healing.value <= 1.0 else int(healing.value)
    old = servant["current_hp"]
    servant["current_hp"] = min(servant["current_hp"] + amount, servant["max_hp"])
    healed = servant["current_hp"] - old
    if healed > 0:
        battle_log.append(f"{format_name(servant)} recovers {healed} HP.")
    return healed


def process_sacrifice_effects(servant: dict, battle_log: list) -> bool:
    if not get_effect(servant, EffectType.SACRIFICE):
        return False
    servant["current_hp"] = 0
    servant["alive"] = False
    battle_log.append(f"{format_name(servant)} sacrifices themselves!")
    return True


def can_act(servant: dict) -> bool:
    return not has_effect(servant, EffectType.STUN) and not has_effect(servant, EffectType.SLEEP)


def check_instant_kill(attacker: dict, defender: dict, battle_log: list) -> bool:
    ik = get_effect(attacker, EffectType.INSTANT_KILL)
    if not ik:
        return False
    if random.random() < ik.value:
        if defender.get("ik_immune"):  # e.g. raid bosses: can't be one-shot out of their battle HP
            battle_log.append(f"{format_name(defender)} resists instant death!")
            return False
        defender["current_hp"] = 0
        battle_log.append(f"{format_name(attacker)} instantly defeats {format_name(defender)}!")
        return True
    return False


def apply_piercing_damage(attacker: dict, damage_dealt: int, enemies: list, battle_log: list):
    piercing = get_effect(attacker, EffectType.PIERCING)
    if not piercing or damage_dealt <= 0:
        return
    front = get_next_alive_servant(enemies)
    if not front:
        return
    backline = [s for s in enemies if s["alive"] and s["servant_id"] != front["servant_id"]]
    pierce = int(damage_dealt * piercing.value)
    for s in backline:
        s["current_hp"] = max(0, s["current_hp"] - pierce)
        battle_log.append(f"{format_name(attacker)}'s piercing hits {format_name(s)} for {pierce}.")


def process_order_change(servants: list, battle_log: list):
    for servant in servants:
        if not servant["alive"]:
            continue
        oc = get_effect(servant, EffectType.ORDER_CHANGE)
        if not oc:
            continue
        others = [s for s in servants if s["alive"] and s["servant_id"] != servant["servant_id"]]
        if not others:
            continue
        if oc.value == -1:
            max_pos = max(s["position"] for s in servants if s["alive"])
            if servant["position"] < max_pos:
                old = servant["position"]
                servant["position"] = max_pos
                for s in servants:
                    if s["alive"] and s["servant_id"] != servant["servant_id"] and old < s["position"] <= max_pos:
                        s["position"] -= 1
                servants.sort(key=lambda s: s["position"])
                battle_log.append(f"{format_name(servant)} moves to the back.")
        elif oc.value == 1:
            min_pos = min(s["position"] for s in servants if s["alive"])
            if servant["position"] > min_pos:
                old = servant["position"]
                servant["position"] = min_pos
                for s in servants:
                    if s["alive"] and s["servant_id"] != servant["servant_id"] and min_pos <= s["position"] < old:
                        s["position"] += 1
                servants.sort(key=lambda s: s["position"])
                battle_log.append(f"{format_name(servant)} moves to the front.")
        else:
            target = random.choice(others)
            servant["position"], target["position"] = target["position"], servant["position"]
            servants.sort(key=lambda s: s["position"])
            battle_log.append(f"{format_name(servant)} swaps places with {format_name(target)}.")


def pass_buffs_on_death(dying: dict, team: list, battle_log: list):
    if not get_effect(dying, EffectType.PASS_BUFFS):
        return
    nxt = next((s for s in team if s["alive"] and s["servant_id"] != dying["servant_id"]), None)
    if not nxt:
        return
    passed = []
    for key, data in dying["active_buffs"].items():
        if key == EffectType.PASS_BUFFS.value:
            continue
        nxt["active_buffs"][key] = data.copy() if isinstance(data, dict) else list(data)
        passed.append(key.replace("_", " ").title())
    if passed:
        battle_log.append(f"{format_name(dying)}'s buffs pass to {format_name(nxt)}.")


def apply_on_damage_effects(servant: dict, damage_dealt: int, battle_log: list):
    hod = get_effect(servant, EffectType.HEAL_ON_DAMAGE)
    if hod:
        servant["current_hp"] = min(servant["current_hp"] + int(hod.value), servant["max_hp"])
    aod = get_effect(servant, EffectType.ATK_UP_ON_DAMAGE)
    if aod and damage_dealt > 0:
        apply_effect(servant, Effect(EffectType.ATTACK_UP, aod.value, aod.buff_duration))


def apply_on_hurt_effects(servant: dict, damage_taken: int, battle_log: list, team: "list | None" = None):
    aoh = get_effect(servant, EffectType.ATK_UP_ON_HURT)
    if aoh and damage_taken > 0:
        apply_effect(servant, Effect(EffectType.ATTACK_UP, aoh.value, aoh.buff_duration))
    item_id = servant.get("equipped_item")
    if item_id and damage_taken > 0:
        item = get_item(item_id)
        if item and item.get("trigger") == "on_hurt":
            ed = item.get("effect", {}) or {}
            etype = ed.get("type")
            if etype == "CONDITIONAL_HEAL" and ed.get("condition") == "hp_below_50" and servant["alive"]:
                if servant["current_hp"] / servant["max_hp"] < 0.5:
                    heal = int(servant["max_hp"] * ed.get("value", 0))
                    servant["current_hp"] = min(servant["max_hp"], servant["current_hp"] + heal)
                    battle_log.append(f"{item.get('emoji','')} {format_name(servant)} heals {heal} HP.")
                    servant["equipped_item"] = None
            elif etype == "ORDER_CHANGE_BACK" and ed.get("condition") == "position_0_only" and servant["position"] == 0 and team:
                max_pos = max(s["position"] for s in team if s["alive"])
                if max_pos > 0:
                    old = servant["position"]
                    servant["position"] = max_pos
                    for s in team:
                        if s["alive"] and s["servant_id"] != servant["servant_id"] and old < s["position"] <= max_pos:
                            s["position"] -= 1
                    team.sort(key=lambda s: s["position"])
                    battle_log.append(f"{item.get('emoji','')} {format_name(servant)} ejects to the back!")
                    servant["equipped_item"] = None


def apply_on_kill_effects(attacker, defeated, attacker_team, enemy_team, battle_log):
    auk = get_effect(attacker, EffectType.ATK_UP_ON_KILL)
    if auk:
        apply_effect(attacker, Effect(EffectType.ATTACK_UP, auk.value, auk.buff_duration))
    duk = get_effect(attacker, EffectType.DEF_UP_ON_KILL)
    if duk:
        apply_effect(attacker, Effect(EffectType.DEFENSE_UP, duk.value, duk.buff_duration))
    check_and_trigger_skills(ON_KILL, attacker, attacker_team, enemy_team, battle_log)


def apply_item_effects(servant: dict, trigger: str, battle_log: list):
    item = get_item(servant.get("equipped_item"))
    if not item or item.get("trigger") != trigger:
        return
    emoji, name = item.get("emoji", ""), item.get("name", "Item")
    effects = item.get("effects") or ([item["effect"]] if item.get("effect") else [])
    for ed in effects:
        _apply_item_effect(servant, ed, emoji, name, battle_log)
    if item.get("consumable") and trigger == "on_enter":
        servant["equipped_item"] = None


_ITEM_EFFECT_MAP = {
    "ATTACK_UP": EffectType.ATTACK_UP,
    "DEFENSE_UP": EffectType.DEFENSE_UP,
    "EVADE": EffectType.EVADE,
    "GUTS_PIERCE": EffectType.GUTS_PIERCE,
}


def _apply_item_effect(servant, ed, emoji, name, battle_log):
    t = ed.get("type")
    val = ed.get("value", 0)
    dur = ed.get("duration", 1)
    if t in _ITEM_EFFECT_MAP:
        apply_effect(servant, Effect(_ITEM_EFFECT_MAP[t], val, dur))
    elif t == "HP_DOWN":
        cut = int(servant["max_hp"] * val)
        servant["max_hp"] -= cut
        servant["current_hp"] = min(servant["current_hp"], servant["max_hp"])
    elif t == "HEAL_PER_TURN":
        apply_effect(servant, Effect(EffectType.HEALING_PER_TURN, val, dur))
    elif t == "DAMAGE_PER_TURN":
        apply_effect(servant, Effect(EffectType.CURSE, val, dur))
    elif t == "GUTS":
        if not has_effect(servant, EffectType.GUTS):
            apply_effect(servant, Effect(EffectType.GUTS, val, dur))
    elif t == "SELF_STUN":
        apply_effect(servant, Effect(EffectType.STUN, val, dur))
    elif t == "MAX_HP_UP":
        apply_effect(servant, Effect(EffectType.MAX_HP_UP, val, dur))
    battle_log.append(f"{emoji} {name} activates on {format_name(servant)}.")


def calculate_damage_with_effects(attacker: dict, defender: dict, variance: bool = True) -> int:
    """Damage from attacker to defender: ATK, attack buffs/debuffs, variance, class, defense."""
    damage = float(attacker["atk"])
    up = get_effect(attacker, EffectType.ATTACK_UP)
    if up:
        damage *= 1.0 + up.value
    down = get_effect(attacker, EffectType.ATTACK_DOWN)
    if down:
        damage *= 1.0 - down.value
    if get_effect(attacker, EffectType.BURN):
        damage *= 0.9
    if variance:
        damage *= random.uniform(0.9, 1.1)
    damage *= class_advantage(attacker["className"], defender["className"])
    dmult = 1.0
    def_up = get_effect(defender, EffectType.DEFENSE_UP)
    if def_up:
        dmult *= 1.0 - def_up.value
    def_down = get_effect(defender, EffectType.DEFENSE_DOWN)
    if def_down:
        dmult *= 1.0 + def_down.value
    if get_effect(defender, EffectType.CURSE):
        dmult *= 1.1
    return int(damage * dmult)


def _handle_death(dying, attacker, attacker_can_act, own_team, enemy_team, battle_log) -> bool:
    """Resolve a servant at <=0 HP: guts revive, else on-kill + on-defeat + death. Returns died."""
    guts_pierce = has_effect(attacker, EffectType.GUTS_PIERCE)
    guts = get_effect(dying, EffectType.GUTS)
    if guts and guts.value > 0 and not guts_pierce:
        dying["current_hp"] = 1
        guts.value -= 1
        dying["active_buffs"][EffectType.GUTS.value] = guts.to_dict()
        battle_log.append(f"{format_name(dying)}'s Guts revives them at 1 HP.")
        return False
    if attacker_can_act:
        apply_on_kill_effects(attacker, dying, enemy_team, own_team, battle_log)
    dying["current_hp"] = 0
    battle_log.append(f"{format_name(dying)} is defeated!")
    if attacker_can_act:
        check_and_trigger_skills(ON_DEFEAT, dying, own_team, enemy_team, battle_log)
    pass_buffs_on_death(dying, own_team, battle_log)
    dying["alive"] = False
    return True


def _outcome(battle_state, player, enemy, battle_log) -> bool:
    """Set victory/draw/game_over if the battle is decided. Returns True if over."""
    pa = any(s["alive"] for s in player)
    ea = any(s["alive"] for s in enemy)
    if pa and ea:
        return False
    battle_state["game_over"] = True
    if not pa and not ea:
        battle_state["draw"] = True
        battle_log.append("Draw -- both teams eliminated.")
    elif not ea:
        battle_state["victory"] = True
        battle_log.append("Victory!")
    else:
        battle_log.append("Defeat.")
    return True


def resolve(battle_state: dict, max_turns: int = 50) -> dict:
    """Auto-resolve the battle (simultaneous attacks, left-to-right). Mutates + returns state."""
    player = battle_state["player_servants"]
    enemy = battle_state["enemy_servants"]
    log = battle_state["battle_log"]
    player_active = enemy_active = None
    turn = 0

    trigger_battle_start_skills(player, enemy, log)

    # skill-seal pre-scan for the starting frontliners
    def _front_seals(team):
        f = next((s for s in team if s["position"] == 0 and s["alive"]), None)
        return bool(f and f.get("skill") and f["skill"].trigger == ON_ENTER
                    and _skill_has_effect(f["skill"], "skill_seal"))

    player_sealed = _front_seals(enemy)
    enemy_sealed = _front_seals(player)

    for s in player:
        if s["position"] == 0 and s["alive"]:
            log.append(f"{format_name(s)} enters the battlefield.")
            check_and_trigger_skills(ON_ENTER, s, player, enemy, log, sealed_by_enemy=player_sealed)
            apply_item_effects(s, "on_enter", log)
    for s in enemy:
        if s["position"] == 0 and s["alive"]:
            log.append(f"{format_name(s)} enters the battlefield.")
            check_and_trigger_skills(ON_ENTER, s, enemy, player, log, sealed_by_enemy=enemy_sealed)
            apply_item_effects(s, "on_enter", log)

    while turn < max_turns:
        log.append(f"-- Turn {turn + 1} --")
        for s in player + enemy:
            if s["alive"]:
                process_dot_effects(s, log)
                process_healing_effects(s, log)
        for s in player + enemy:
            if s["alive"] and s["current_hp"] <= 0:
                guts = get_effect(s, EffectType.GUTS)
                if guts and guts.value > 0:
                    s["current_hp"] = 1
                    guts.value -= 1
                    s["active_buffs"][EffectType.GUTS.value] = guts.to_dict()
                    log.append(f"{format_name(s)}'s Guts revives them at 1 HP.")
                else:
                    s["current_hp"] = 0
                    s["alive"] = False
                    log.append(f"{format_name(s)} succumbs to damage over time.")
        if _outcome(battle_state, player, enemy, log):
            break

        pf = get_next_alive_servant(player)
        ef = get_next_alive_servant(enemy)
        if not pf or not ef:
            break

        # log new frontliners entering + their ON_ENTER
        for team, foes, front, active, sealed_flag_holder in (
            (player, enemy, pf, player_active, "p"),
            (enemy, player, ef, enemy_active, "e"),
        ):
            if active is not None and active != front["servant_id"]:
                log.append(f"{format_name(front)} enters the battlefield.")
                foe_front = get_next_alive_servant(foes)
                sealed = bool(foe_front and foe_front.get("skill")
                              and foe_front["skill"].trigger == ON_ENTER
                              and _skill_has_effect(foe_front["skill"], "skill_seal"))
                check_and_trigger_skills(ON_ENTER, front, team, foes, log, sealed_by_enemy=sealed)
                apply_item_effects(front, "on_enter", log)
        player_active, enemy_active = pf["servant_id"], ef["servant_id"]

        p_act, e_act = can_act(pf), can_act(ef)
        if not p_act:
            log.append(f"{format_name(pf)} cannot act.")
        if not e_act:
            log.append(f"{format_name(ef)} cannot act.")
        if not p_act and not e_act:
            for s in player + enemy:
                if s["alive"]:
                    process_effect_durations(s)
            turn += 1
            continue

        p_dmg = calculate_damage_with_effects(pf, ef) if p_act else 0
        e_dmg = calculate_damage_with_effects(ef, pf) if e_act else 0

        for atk, dfn, dmg, act, foes in ((pf, ef, p_dmg, p_act, enemy), (ef, pf, e_dmg, e_act, player)):
            if not act or dfn["current_hp"] <= 0:
                continue
            if has_effect(dfn, EffectType.ANTI_PURGE):
                log.append(f"{format_name(dfn)}'s Anti-Purge negates the hit.")
                continue
            if has_effect(dfn, EffectType.EVADE) and not has_effect(atk, EffectType.IGNORE_EVADE):
                log.append(f"{format_name(dfn)} evades.")
                continue
            if check_instant_kill(atk, dfn, log):
                continue
            dfn["current_hp"] -= dmg
            log.append(
                f"{format_name(atk)} hits {format_name(dfn)} for {dmg} "
                f"[{max(0, dfn['current_hp'])}/{dfn['max_hp']}]"
            )
            if dmg > 0:
                apply_on_damage_effects(atk, dmg, log)
                apply_on_hurt_effects(dfn, dmg, log, foes)
            apply_piercing_damage(atk, dmg, foes, log)

        if pf["current_hp"] <= 0:
            _handle_death(pf, ef, e_act, player, enemy, log)
        if ef["current_hp"] <= 0:
            _handle_death(ef, pf, p_act, enemy, player, log)

        if _outcome(battle_state, player, enemy, log):
            break

        if pf["alive"]:
            process_sacrifice_effects(pf, log)
        if ef["alive"]:
            process_sacrifice_effects(ef, log)
        process_order_change(player, log)
        process_order_change(enemy, log)
        for s in player + enemy:
            if s["alive"]:
                process_effect_durations(s)
        turn += 1

    if turn >= max_turns and not battle_state["game_over"]:
        battle_state["game_over"] = True
        php = sum(s["current_hp"] for s in player)
        ehp = sum(s["current_hp"] for s in enemy)
        battle_state["victory"] = php > ehp
        battle_state["draw"] = php == ehp
        log.append(f"Time up after {max_turns} turns -- {'you win' if php > ehp else 'enemy wins' if ehp > php else 'draw'} on HP ({php} vs {ehp}).")

    battle_state["current_turn"] = turn
    return battle_state
