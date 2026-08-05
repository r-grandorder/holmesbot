"""Autobattle cog (experimental). Loaded only when AUTOBATTLE_ENABLED is set (see bot.py), so it
ships dark.

Everything lives under one /ab group:
  /ab team   -- set/view your 1-3 servant team + loadout
  /ab fight  -- fight a preconfigured PvE boss stage; small capped QP on a win
  /ab duel   -- battle another player's team (PvP); cross-faction wins score war points, own cap
  /ab shop   -- spend QP on consumable battle items (the QP sink)
  /ab equip  -- equip/unequip an item to one of your team's servants

Uses the contract feature's roster (servant_contracts) + QP (scores), so it shares the
contract-access gate. PvE and PvP each pay a small, separately-capped QP reward (PvP also scores
war points), but shop items are the real QP sink: every item is a per-battle consumable spent for
each fight it's equipped for (win or lose, whether or not it triggered). If you own more it stays
equipped (auto-re-equip), otherwise it auto-unequips. In PvP only the challenger's items are spent.
"""
from __future__ import annotations

import io
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from branding import qp
from data import autobattle
from data import autobattle_engine as engine
from data import autobattle_items as items
from data import contract_game as cg
from data import images
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


class LogPager(discord.ui.View):
    """Flips through a long battle log. Public -- anyone can page it, not just the fighter."""

    def __init__(self, embeds: "list[discord.Embed]") -> None:
        super().__init__(timeout=600)
        self.embeds = embeds
        self.i = 0
        self._sync()

    def _sync(self) -> None:
        self.prev.disabled = self.i <= 0
        self.next.disabled = self.i >= len(self.embeds) - 1

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.i = max(0, self.i - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embeds[self.i], view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.i = min(len(self.embeds) - 1, self.i + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embeds[self.i], view=self)


class Autobattle(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}  # PvE fight cooldown
        self._pvp_cd: dict[tuple[int, int], float] = {}  # (guild, challenger) -> last /ab duel
        self._pvp_pair_cd: dict = {}  # (guild, frozenset{a,b}) -> last /ab duel between them

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

    async def _side(self, gid, uid, team_ids) -> "list[dict]":
        """A player's combatants: their battle_team at current levels, with only owned items equipped."""
        levels = await self._owned_levels(gid, uid)
        inv = {r["item_id"]: r["quantity"] for r in await self.bot.contracts.inventory(gid, uid)}
        equips = {
            sid: iid
            for sid, iid in (await self.bot.contracts.equips(gid, uid)).items()
            if inv.get(iid, 0) > 0
        }
        return self._build_team([(sid, levels.get(sid, 1)) for sid in team_ids], equips)

    @staticmethod
    def _hp_bar(cur: int, mx: int, length: int = 10) -> str:
        if mx <= 0:
            return "\N{LIGHT SHADE}" * length
        filled = max(0, min(length, round(length * max(0, cur) / mx)))
        return "\N{DARK SHADE}" * filled + "\N{LIGHT SHADE}" * (length - filled)

    def _team_hp(self, combatants) -> str:
        """Post-battle HP bars for a team: one line per servant (class emoji + name + bar + HP)."""
        lines = []
        for c in combatants:
            cur = max(0, c["current_hp"])
            ko = " (KO)" if not c["alive"] else ""
            lines.append(
                f"{engine.format_name(c)} `{self._hp_bar(cur, c['max_hp'])}` "
                f"{cur:,}/{c['max_hp']:,}{ko}"
            )
        return "\n".join(lines) or "(none)"

    async def _faces(self, session, servant_ids) -> "list[bytes]":
        out = []
        for sid in servant_ids:
            s = self.bot.servants.get(sid)
            if s and s.face:
                try:
                    out.append(await images.fetch_bytes(session, s.face))
                except Exception:  # a face that won't fetch just drops from the composite
                    pass
        return out

    async def _battle_image(self, bg_url, left_ids, right_ids) -> "discord.File | None":
        """Composite both teams' faces over the battle background -> a discord.File (battle.png),
        or None on any fetch/decode failure (the image is cosmetic; the caller falls back)."""
        session = self.bot.http_session
        if not session or not bg_url:
            return None
        try:
            bg = await images.fetch_bytes(session, bg_url)
            png = images.battle_preview(
                bg, await self._faces(session, left_ids), await self._faces(session, right_ids)
            )
            return discord.File(io.BytesIO(png), filename="battle.png")
        except Exception:
            return None

    async def _consume_items(self, gid, uid, player_team, initial) -> "list[str]":
        """Every item equipped for the fight is spent afterward -- one copy each, win or lose,
        whether or not it triggered. Auto-unequip when the last copy is gone. Returns display lines."""
        lines = []
        for c in player_team:
            was = initial.get(c["servant_id"])
            if not was:
                continue
            remaining = await self.bot.contracts.add_item(gid, uid, was, -1)
            if remaining <= 0:
                await self.bot.contracts.unequip_item_everywhere(gid, uid, was)
            tail = "out of stock" if remaining <= 0 else f"{remaining} left"
            lines.append(f"{_item_tag(was)} ({tail})")
        return lines

    @staticmethod
    def _paginate_log(lines, limit=2000) -> "list[str]":
        """Split the full battle log into page-sized chunks on line boundaries -- nothing is lost,
        the whole log is browsable via the pager. Returns at least one (possibly empty) page."""
        pages, cur, length = [], [], 0
        for ln in lines:
            ln = ln if len(ln) <= limit else ln[:limit]
            if cur and length + len(ln) + 1 > limit:
                pages.append("\n".join(cur))
                cur, length = [], 0
            cur.append(ln)
            length += len(ln) + 1
        if cur:
            pages.append("\n".join(cur))
        return pages or [""]

    def _battle_embed(self, member, enc, state, log_text, item_lines, reward_line=None, has_image=False) -> discord.Embed:
        won, draw = state["victory"], state.get("draw")
        color = (
            discord.Color.green() if won
            else discord.Color.light_grey() if draw
            else discord.Color.red()
        )
        outcome = "Victory!" if won else "Draw" if draw else "Defeat"
        embed = discord.Embed(
            title=f"{enc.get('difficulty', '?').title()}: {enc.get('name', 'Stage')}",
            description=log_text,  # plain text so emojis + bold render
            color=color,
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        if has_image:
            embed.set_image(url="attachment://battle.png")
        elif enc.get("bg_image"):
            embed.set_image(url=enc["bg_image"])
        embed.add_field(name="Your team", value=self._team_hp(state["player_servants"]), inline=False)
        embed.add_field(name="Enemy", value=self._team_hp(state["enemy_servants"]), inline=False)
        embed.add_field(name="Result", value=outcome, inline=True)
        if reward_line:
            embed.add_field(name="Reward", value=reward_line, inline=True)
        if item_lines:
            embed.add_field(name="Items used", value="\n".join(item_lines), inline=False)
        return embed

    @ab.command(name="fight", description="Fight a preconfigured PvE boss stage with your team.")
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

        player_team = await self._side(gid, uid, team_ids)
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

        reward_line = None
        if state["victory"]:
            amount = cg.AB_PVE_REWARD.get(enc.get("difficulty", ""), 0)
            if amount > 0:
                count = await self.bot.contracts.ab_reward_count(gid, uid, "pve")
                if count < cg.AB_PVE_DAILY_CAP:
                    await self.bot.scoring.add_qp(gid, uid, amount)
                    await self.bot.contracts.bump_ab_reward(gid, uid, "pve")
                    reward_line = f"+{qp(amount)}"
                else:
                    reward_line = f"Daily PvE cap reached ({cg.AB_PVE_DAILY_CAP})"

        enemy_ids = [m["servant_id"] for m in enc.get("servants", [])]
        battle_file = await self._battle_image(enc.get("bg_image"), team_ids, enemy_ids)
        pages = self._paginate_log(state["battle_log"])
        embeds = []
        for idx, page in enumerate(pages):
            e = self._battle_embed(
                interaction.user, enc, state, page, item_lines, reward_line,
                has_image=battle_file is not None,
            )
            if len(pages) > 1:
                e.set_footer(text=f"Log page {idx + 1}/{len(pages)}")
            embeds.append(e)
        send_kwargs = {"file": battle_file or discord.utils.MISSING}
        if len(embeds) > 1:
            send_kwargs["view"] = LogPager(embeds)
        await interaction.followup.send(embed=embeds[0], **send_kwargs)

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

    # --- /ab duel (PvP) --------------------------------------------------------------------------

    async def _ab_war_points(self, gid, winner, loser) -> str:
        """A cross-faction /ab duel win scores for the winner's faction during an active war.
        Returns a line for the embed, or '' if it doesn't apply (no war / not both in factions /
        same faction)."""
        if not await self.bot.wars.active(gid):
            return ""
        wm = await self.bot.wars.member(gid, winner.id)
        lm = await self.bot.wars.member(gid, loser.id)
        if wm is None or lm is None or wm["slot"] == lm["slot"]:
            return ""
        await self.bot.wars.add_points(gid, winner.id, cg.AB_PVP_WAR_POINTS)
        return f"\n+{cg.AB_PVP_WAR_POINTS} war points for **{wm['name']}**."

    def _pvp_embed(
        self, challenger, opponent, state, winner, log_text, item_lines, reward_line,
        bg_url=None, has_image=False,
    ) -> discord.Embed:
        draw = state.get("draw")
        color = (
            discord.Color.light_grey() if draw
            else discord.Color.green() if winner is not None and winner.id == challenger.id
            else discord.Color.red()
        )
        outcome = "Draw." if draw else f"{winner.display_name} wins!"
        embed = discord.Embed(
            title=f"{challenger.display_name} vs {opponent.display_name}",
            description=log_text,  # plain text so emojis + bold render
            color=color,
        )
        if has_image:
            embed.set_image(url="attachment://battle.png")
        elif bg_url:
            embed.set_image(url=bg_url)
        embed.add_field(name=challenger.display_name, value=self._team_hp(state["player_servants"]), inline=False)
        embed.add_field(name=opponent.display_name, value=self._team_hp(state["enemy_servants"]), inline=False)
        embed.add_field(name="Result", value=outcome, inline=True)
        if reward_line:
            embed.add_field(name="Reward", value=reward_line, inline=False)
        if item_lines:
            embed.add_field(
                name=f"{challenger.display_name}'s items used", value="\n".join(item_lines), inline=False
            )
        return embed

    @ab.command(
        name="duel",
        description="Battle another player's autobattle team. Cross-faction wins score war points.",
    )
    @app_commands.describe(opponent="The player to challenge")
    async def duel(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if opponent.bot or opponent.id == interaction.user.id:
            return await interaction.response.send_message("Pick another player to duel.", ephemeral=True)
        if not self._allowed(opponent.id):
            return await interaction.response.send_message(
                f"{opponent.display_name} isn't in the autobattle feature yet.", ephemeral=True
            )
        gid, uid = interaction.guild_id, interaction.user.id
        my_team = await self.bot.contracts.battle_team(gid, uid)
        if not my_team:
            return await interaction.response.send_message("Set a team first with /ab team.", ephemeral=True)
        opp_team = await self.bot.contracts.battle_team(gid, opponent.id)
        if not opp_team:
            return await interaction.response.send_message(
                f"{opponent.display_name} has no autobattle team set.", ephemeral=True
            )

        now = time.monotonic()
        if now - self._pvp_cd.get((gid, uid), 0.0) < cg.AB_PVP_COOLDOWN:
            return await interaction.response.send_message(
                "You're dueling too fast -- give it a moment.", ephemeral=True
            )
        pair = (gid, frozenset((uid, opponent.id)))
        pair_wait = cg.AB_PVP_PAIR_COOLDOWN - (now - self._pvp_pair_cd.get(pair, 0.0))
        if pair_wait > 0:
            return await interaction.response.send_message(
                f"You dueled {opponent.display_name} recently -- try again in {int(pair_wait)}s.",
                ephemeral=True,
            )
        self._pvp_cd[(gid, uid)] = now
        self._pvp_pair_cd[pair] = now

        await interaction.response.defer()

        challenger_team = await self._side(gid, uid, my_team)
        defender_team = await self._side(gid, opponent.id, opp_team)
        if not challenger_team or not defender_team:
            return await interaction.followup.send("A team's servants are unavailable.")

        state = engine.build_state(challenger_team, defender_team)
        # attacker-only consumption: only the challenger spends items on a fight they started.
        initial = {c["servant_id"]: c.get("equipped_item") for c in challenger_team}
        engine.resolve(state)
        item_lines = await self._consume_items(gid, uid, challenger_team, initial)

        if state.get("draw"):
            winner = loser = None
        elif state["victory"]:
            winner, loser = interaction.user, opponent
        else:
            winner, loser = opponent, interaction.user

        reward_line = None
        if winner is not None:
            count = await self.bot.contracts.ab_reward_count(gid, winner.id, "pvp")
            if count < cg.AB_PVP_DAILY_CAP:
                await self.bot.scoring.add_qp(gid, winner.id, cg.AB_PVP_QP)
                await self.bot.contracts.bump_ab_reward(gid, winner.id, "pvp")
                reward_line = f"{winner.display_name} earns {qp(cg.AB_PVP_QP)}."
                reward_line += await self._ab_war_points(gid, winner, loser)
            else:
                reward_line = f"{winner.display_name} hit the daily PvP cap ({cg.AB_PVP_DAILY_CAP}) -- no reward."

        bgs = autobattle.pvp_backgrounds()
        bg_url = random.choice(bgs).get("bg_image") if bgs else None
        battle_file = await self._battle_image(bg_url, my_team, opp_team)
        pages = self._paginate_log(state["battle_log"])
        embeds = []
        for idx, page in enumerate(pages):
            e = self._pvp_embed(
                interaction.user, opponent, state, winner, page, item_lines, reward_line,
                bg_url=bg_url, has_image=battle_file is not None,
            )
            if len(pages) > 1:
                e.set_footer(text=f"Log page {idx + 1}/{len(pages)}")
            embeds.append(e)
        send_kwargs = {
            "file": battle_file or discord.utils.MISSING,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if len(embeds) > 1:
            send_kwargs["view"] = LogPager(embeds)
        await interaction.followup.send(embed=embeds[0], **send_kwargs)


async def setup(bot) -> None:
    await bot.add_cog(Autobattle(bot))
