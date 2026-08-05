"""Autobattle cog (experimental). Loaded only when AUTOBATTLE_ENABLED is set (see bot.py), so it
ships dark.

Everything lives under one /ab group:
  /ab team   -- set/view your 1-3 servant team + loadout
  /ab fight  -- fight a preconfigured PvE stage (free); win to earn XP for the whole team
  /ab shop   -- spend QP on consumable battle items (the QP sink)
  /ab equip  -- equip/unequip an item to one of your team's servants

Uses the contract feature's roster (servant_contracts) + QP (scores), so it shares the
contract-access gate. Battling is free; shop items are the only QP sink. Items are consumable: an
on-enter buff is spent each battle, a conditional item only when it fires; if you own more it
stays equipped (auto-re-equip), otherwise it auto-unequips.
"""
from __future__ import annotations

import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from branding import qp
from data import autobattle
from data import autobattle_engine as engine
from data import autobattle_items as items
from data.servants import class_display

_DENY = "The autobattle feature isn't open to you yet."
# Seconds between fights per user: anti-spam + a light rate-limit on XP farming. Tunable.
AUTOBATTLE_COOLDOWN = 20
_DIFFICULTY_ORDER = {"beginner": 0, "intermediate": 1, "hard": 2}
_UNEQUIP = "__unequip__"


def _item_tag(item_id: "str | None") -> str:
    """'emoji Name' for an item id, or '' if unknown/none."""
    it = items.get_item(item_id)
    if not it:
        return ""
    return f"{it.get('emoji', '')} {it['name']}".strip()


class _BuySelect(discord.ui.Select):
    def __init__(self, cog: "Autobattle") -> None:
        self.cog = cog
        opts = [
            discord.SelectOption(
                label=it["name"][:100],
                value=iid,
                description=f"{int(it.get('price', 0)):,} QP"[:100],
                emoji=it.get("emoji") or None,
            )
            for iid, it in sorted(items.all_items().items(), key=lambda kv: kv[1].get("price", 0))
        ]
        super().__init__(
            placeholder="Buy an item (spends QP)...", min_values=1, max_values=1, options=opts[:25]
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog._buy(interaction, self.values[0])


class ShopView(discord.ui.View):
    """Invoker-scoped buy dropdown for /ab shop (ephemeral)."""

    def __init__(self, cog: "Autobattle", user_id: int) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.add_item(_BuySelect(cog))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This shop isn't yours.", ephemeral=True)
            return False
        return True


class Autobattle(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}

    ab = app_commands.Group(name="ab", description="Autobattle (experimental).", guild_only=True)

    def _allowed(self, user_id: int) -> bool:
        return self.bot.config.contract_open or user_id in self.bot.config.contract_whitelist

    async def _owned_levels(self, guild_id: int, user_id: int) -> "dict[int, int]":
        return {
            r["servant_id"]: r["level"]
            for r in await self.bot.contracts.owned(guild_id, user_id)
        }

    # --- /ab team --------------------------------------------------------------------------------

    def _team_embed(self, member, servant_ids, levels, equips) -> discord.Embed:
        lines = []
        for i, sid in enumerate(servant_ids, 1):
            s = self.bot.servants.get(sid)
            if s is None:
                lines.append(f"**{i}.** Servant #{sid} (unavailable)")
                continue
            lvl = levels.get(sid, 1)
            atk, hp = autobattle.battle_stats(s, lvl)
            tag = _item_tag(equips.get(sid))
            eq = f" -- {tag}" if tag else ""
            lines.append(
                f"**{i}.** {s.name} ({class_display(s.class_name) or '?'}) "
                f"Lv {lvl} -- {atk:,} ATK / {hp:,} HP{eq}"
            )
        return discord.Embed(
            title=f"{member.display_name}'s Autobattle Team",
            description="\n".join(lines) or "(empty)",
            color=discord.Color.blurple(),
        )

    @ab.command(
        name="team", description="Set or view your autobattle team (1-3 servants, front to back)."
    )
    @app_commands.describe(
        first="Front servant (leave all blank to view your current team)",
        second="Middle servant",
        third="Back servant",
    )
    async def team(
        self,
        interaction: discord.Interaction,
        first: "int | None" = None,
        second: "int | None" = None,
        third: "int | None" = None,
    ) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id
        picks = [x for x in (first, second, third) if x is not None]
        levels = await self._owned_levels(gid, uid)

        if not picks:
            team = await self.bot.contracts.battle_team(gid, uid)
            if not team:
                return await interaction.response.send_message(
                    "You have no autobattle team yet. Set one with /ab team first:<servant> (up to three).",
                    ephemeral=True,
                )
            equips = await self.bot.contracts.equips(gid, uid)
            return await interaction.response.send_message(
                embed=self._team_embed(interaction.user, team, levels, equips), ephemeral=True
            )

        chosen: list[int] = []
        for sid in picks:
            if sid not in levels:
                s = self.bot.servants.get(sid)
                name = s.name if s else f"#{sid}"
                return await interaction.response.send_message(
                    f"You haven't contracted {name} -- pick from your own servants.", ephemeral=True
                )
            if sid not in chosen:  # a servant can't hold two slots
                chosen.append(sid)
        await self.bot.contracts.set_battle_team(gid, uid, chosen)
        equips = await self.bot.contracts.equips(gid, uid)
        await interaction.response.send_message(
            content="Autobattle team saved.",
            embed=self._team_embed(interaction.user, chosen, levels, equips),
            ephemeral=True,
        )

    async def _team_autocomplete(self, interaction, current):
        q = current.strip().lower()
        out: list[app_commands.Choice[int]] = []
        for r in await self.bot.contracts.owned(interaction.guild_id, interaction.user.id):
            s = self.bot.servants.get(r["servant_id"])
            if s is None or (q and q not in s.name.lower()):
                continue
            out.append(app_commands.Choice(name=f"{s.name[:70]} (Lv {r['level']})", value=s.id))
            if len(out) >= 25:
                break
        return out

    @team.autocomplete("first")
    async def _ac_t1(self, interaction, current):
        return await self._team_autocomplete(interaction, current)

    @team.autocomplete("second")
    async def _ac_t2(self, interaction, current):
        return await self._team_autocomplete(interaction, current)

    @team.autocomplete("third")
    async def _ac_t3(self, interaction, current):
        return await self._team_autocomplete(interaction, current)

    # --- /ab shop --------------------------------------------------------------------------------

    async def _shop_embed(self, gid, uid, note=None) -> discord.Embed:
        bal = await self.bot.scoring.get_balance(gid, uid)
        owned = {r["item_id"]: r["quantity"] for r in await self.bot.contracts.inventory(gid, uid)}
        lines = []
        for iid, it in sorted(items.all_items().items(), key=lambda kv: kv[1].get("price", 0)):
            have = owned.get(iid, 0)
            suffix = f"  (owned: {have})" if have else ""
            emoji = it.get("emoji") or ""
            lines.append(f"{emoji} **{it['name']}** -- {int(it.get('price', 0)):,} QP{suffix}")
            lines.append(f"> {it.get('description', '')}")
        embed = discord.Embed(
            title="Autobattle Item Shop", description="\n".join(lines), color=discord.Color.gold()
        )
        embed.add_field(name="Your QP", value=qp(bal), inline=True)
        if note:
            embed.add_field(name="​", value=note, inline=False)
        return embed

    @ab.command(name="shop", description="Spend QP on consumable battle items.")
    async def shop(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id
        await interaction.response.send_message(
            embed=await self._shop_embed(gid, uid), view=ShopView(self, uid), ephemeral=True
        )

    async def _buy(self, interaction: discord.Interaction, item_id: str) -> None:
        gid, uid = interaction.guild_id, interaction.user.id
        it = items.get_item(item_id)
        if not it:
            return await interaction.response.send_message("Unknown item.", ephemeral=True)
        price = int(it.get("price", 0))
        bal = await self.bot.scoring.get_balance(gid, uid)
        if bal < price:
            return await interaction.response.send_message(
                f"You need {qp(price)} for {it['name']}; you have {qp(bal)}.", ephemeral=True
            )
        await self.bot.scoring.sub_qp(gid, uid, price)
        owned = await self.bot.contracts.add_item(gid, uid, item_id, 1)
        note = f"Bought {_item_tag(item_id)} -- you own {owned} now. Equip it with /ab equip."
        await interaction.response.edit_message(
            embed=await self._shop_embed(gid, uid, note), view=ShopView(self, uid)
        )

    # --- /ab equip -------------------------------------------------------------------------------

    @ab.command(
        name="equip", description="Equip an item to one of your team's servants (or unequip)."
    )
    @app_commands.describe(servant="A servant on your team", item="An item you own, or Unequip")
    async def equip(self, interaction: discord.Interaction, servant: int, item: str) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id
        team = await self.bot.contracts.battle_team(gid, uid)
        if servant not in team:
            return await interaction.response.send_message(
                "That servant isn't on your team. Set your team with /ab team.", ephemeral=True
            )
        s = self.bot.servants.get(servant)
        sname = s.name if s else f"#{servant}"
        if item == _UNEQUIP:
            await self.bot.contracts.unequip(gid, uid, servant)
            return await interaction.response.send_message(f"Unequipped {sname}.", ephemeral=True)
        it = items.get_item(item)
        if not it:
            return await interaction.response.send_message(
                "Unknown item -- pick from the list.", ephemeral=True
            )
        if await self.bot.contracts.item_qty(gid, uid, item) <= 0:
            return await interaction.response.send_message(
                f"You don't own {it['name']}. Buy it in /ab shop.", ephemeral=True
            )
        equips = await self.bot.contracts.equips(gid, uid)
        for other_sid, other_iid in equips.items():
            if other_iid == item and other_sid != servant and other_sid in team:
                other = self.bot.servants.get(other_sid)
                oname = other.name if other else f"#{other_sid}"
                return await interaction.response.send_message(
                    f"{it['name']} is already equipped on {oname}. Only one of each item per team.",
                    ephemeral=True,
                )
        await self.bot.contracts.equip(gid, uid, servant, item)
        await interaction.response.send_message(
            f"Equipped {_item_tag(item)} to {sname}.", ephemeral=True
        )

    @equip.autocomplete("servant")
    async def _ac_equip_servant(self, interaction, current):
        gid, uid = interaction.guild_id, interaction.user.id
        team = await self.bot.contracts.battle_team(gid, uid)
        equips = await self.bot.contracts.equips(gid, uid)
        q = current.strip().lower()
        out: list[app_commands.Choice[int]] = []
        for sid in team:
            s = self.bot.servants.get(sid)
            if s is None or (q and q not in s.name.lower()):
                continue
            tag = _item_tag(equips.get(sid))
            label = s.name + (f" [{tag}]" if tag else "")
            out.append(app_commands.Choice(name=label[:100], value=sid))
        return out

    @equip.autocomplete("item")
    async def _ac_equip_item(self, interaction, current):
        gid, uid = interaction.guild_id, interaction.user.id
        q = current.strip().lower()
        out: list[app_commands.Choice[str]] = [
            app_commands.Choice(name="Unequip (remove item)", value=_UNEQUIP)
        ]
        for r in await self.bot.contracts.inventory(gid, uid):
            it = items.get_item(r["item_id"])
            if not it or (q and q not in it["name"].lower()):
                continue
            out.append(
                app_commands.Choice(name=f"{it['name']} (x{r['quantity']})"[:100], value=r["item_id"])
            )
            if len(out) >= 25:
                break
        return out

    # --- /ab fight -------------------------------------------------------------------------------

    def _build_team(self, id_levels, equips) -> "list[dict]":
        team: list[dict] = []
        pos = 0
        for sid, lvl in id_levels:
            s = self.bot.servants.get(sid)
            if s is None:
                continue
            kit = self.bot.kits.get(sid) if self.bot.kits else None
            team.append(engine.combatant(s, max(1, lvl), pos, kit=kit, item=equips.get(sid)))
            pos += 1
        return team

    async def _consume_items(self, gid, uid, player_team, initial) -> "list[str]":
        """Decrement inventory for items the engine used up (equipped -> None during the fight);
        auto-unequip when the last copy is spent. Returns display lines."""
        lines = []
        for c in player_team:
            was = initial.get(c["servant_id"])
            if was and c.get("equipped_item") is None:
                remaining = await self.bot.contracts.add_item(gid, uid, was, -1)
                if remaining <= 0:
                    await self.bot.contracts.unequip_item_everywhere(gid, uid, was)
                tail = "out of stock" if remaining <= 0 else f"{remaining} left"
                lines.append(f"{_item_tag(was)} ({tail})")
        return lines

    @staticmethod
    def _trim_log(lines, head=6, tail=26, limit=1800) -> str:
        if len(lines) > head + tail:
            skipped = len(lines) - head - tail
            lines = lines[:head] + [f"... ({skipped} more lines) ..."] + lines[-tail:]
        text = "\n".join(lines)
        if len(text) > limit:
            text = text[-limit:]
            nl = text.find("\n")
            text = "..." + text[nl:] if nl != -1 else text
        return text

    def _battle_embed(self, member, enc, state, xp_lines, item_lines) -> discord.Embed:
        won, draw = state["victory"], state.get("draw")
        color = (
            discord.Color.green() if won
            else discord.Color.light_grey() if draw
            else discord.Color.red()
        )
        outcome = "Victory!" if won else "Draw" if draw else "Defeat"
        embed = discord.Embed(
            title=f"{enc.get('difficulty', '?').title()}: {enc.get('name', 'Stage')}",
            description=f"```\n{self._trim_log(state['battle_log'])}\n```",
            color=color,
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        if enc.get("bg_image"):
            embed.set_image(url=enc["bg_image"])
        embed.add_field(name="Result", value=outcome, inline=True)
        if xp_lines:
            embed.add_field(name="XP gained", value="\n".join(xp_lines), inline=False)
        if item_lines:
            embed.add_field(name="Items used", value="\n".join(item_lines), inline=False)
        return embed

    @ab.command(name="fight", description="Fight a PvE stage with your team. Free -- win to earn XP.")
    @app_commands.describe(stage="Which stage to fight (leave blank for a random one)")
    async def fight(self, interaction: discord.Interaction, stage: "str | None" = None) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id

        team_ids = await self.bot.contracts.battle_team(gid, uid)
        if not team_ids:
            return await interaction.response.send_message("Set a team first with /ab team.", ephemeral=True)
        encounters = autobattle.load_encounters()
        if not encounters:
            return await interaction.response.send_message("No stages are available.", ephemeral=True)
        if stage is not None and stage not in encounters:
            return await interaction.response.send_message(
                "Unknown stage -- pick one from the autocomplete.", ephemeral=True
            )
        stage_id = stage if stage is not None else random.choice(list(encounters))
        enc = encounters[stage_id]

        now = time.monotonic()
        last = self._cooldowns.get((gid, uid), 0.0)
        if now - last < AUTOBATTLE_COOLDOWN:
            wait = int(AUTOBATTLE_COOLDOWN - (now - last)) + 1
            return await interaction.response.send_message(
                f"On cooldown -- try again in {wait}s.", ephemeral=True
            )
        self._cooldowns[(gid, uid)] = now

        await interaction.response.defer()

        levels = await self._owned_levels(gid, uid)
        inv = {r["item_id"]: r["quantity"] for r in await self.bot.contracts.inventory(gid, uid)}
        equips = {
            sid: iid
            for sid, iid in (await self.bot.contracts.equips(gid, uid)).items()
            if inv.get(iid, 0) > 0
        }
        player_team = self._build_team([(sid, levels.get(sid, 1)) for sid in team_ids], equips)
        if not player_team:
            return await interaction.followup.send("Your team's servants are unavailable.")
        enemy_team = self._build_team(
            [(m["servant_id"], m["level"]) for m in enc.get("servants", [])], {}
        )
        if not enemy_team:
            return await interaction.followup.send("This stage is misconfigured (no enemies).")

        state = engine.build_state(player_team, enemy_team)
        initial = {c["servant_id"]: c.get("equipped_item") for c in player_team}
        engine.resolve(state)
        item_lines = await self._consume_items(gid, uid, player_team, initial)

        xp_lines: list[str] = []
        if state["victory"]:
            xp = int(enc.get("xp_reward", 0))
            for sid in team_ids:
                res = await self.bot.contracts.add_xp_to(gid, uid, sid, xp)
                if res is None:
                    continue
                old_lvl, new_lvl, cap = res
                s = self.bot.servants.get(sid)
                name = s.name if s else f"#{sid}"
                if new_lvl > old_lvl:
                    mark = " (cap)" if new_lvl >= cap else ""
                    xp_lines.append(f"{name}: +{xp:,} XP -- Lv {old_lvl} -> {new_lvl}{mark}")
                else:
                    xp_lines.append(f"{name}: +{xp:,} XP")

        await interaction.followup.send(
            embed=self._battle_embed(interaction.user, enc, state, xp_lines, item_lines)
        )

    async def _stage_autocomplete(self, interaction, current):
        q = current.strip().lower()
        encs = sorted(
            autobattle.load_encounters().items(),
            key=lambda kv: (_DIFFICULTY_ORDER.get(kv[1].get("difficulty"), 9), kv[1].get("name", "")),
        )
        out: list[app_commands.Choice[str]] = []
        for sid, enc in encs:
            label = f"{enc.get('difficulty', '?').title()}: {enc.get('name', sid)}"
            if q and q not in label.lower() and q not in sid.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=sid))
            if len(out) >= 25:
                break
        return out

    @fight.autocomplete("stage")
    async def _ac_stage(self, interaction, current):
        return await self._stage_autocomplete(interaction, current)


async def setup(bot) -> None:
    await bot.add_cog(Autobattle(bot))
