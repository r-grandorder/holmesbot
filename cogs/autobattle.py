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
each fight it's equipped for (win or lose, whether or not it triggered). Equips are a sticky
loadout -- they stay set even at 0 stock and simply aren't fielded until you restock, so you never
have to re-equip. In PvP only the challenger's items are spent.
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


class ShopView(discord.ui.View):
    """Paginated /ab shop (ephemeral, invoker-scoped), ported from the legacy layout: 5 items per
    page as per-item Buy buttons with the item emote, prev/next nav, and a bulk `quantity` (buy N
    per tap). The embed shows only the current page's items + your balance, so it stays compact."""

    ITEMS_PER_PAGE = 5

    def __init__(self, cog: "Autobattle", guild_id: int, user_id: int, quantity: int = 1) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.gid = guild_id
        self.uid = user_id
        self.quantity = max(1, quantity)
        self.page = 0
        self.items = sorted(items.all_items().items(), key=lambda kv: kv[1].get("price", 0))
        self.pages = max(1, (len(self.items) + self.ITEMS_PER_PAGE - 1) // self.ITEMS_PER_PAGE)
        self._build_buttons()

    def _slice(self):
        s = self.page * self.ITEMS_PER_PAGE
        return self.items[s:s + self.ITEMS_PER_PAGE]

    def _build_buttons(self) -> None:
        self.clear_items()
        prev = discord.ui.Button(
            emoji="◀️", style=discord.ButtonStyle.secondary, row=0, disabled=self.page <= 0
        )
        prev.callback = self._prev
        nxt = discord.ui.Button(
            emoji="▶️", style=discord.ButtonStyle.secondary, row=0, disabled=self.page >= self.pages - 1
        )
        nxt.callback = self._next
        self.add_item(prev)
        self.add_item(nxt)
        for i, (iid, it) in enumerate(self._slice()):
            total = int(it.get("price", 0)) * self.quantity
            qtxt = f"{self.quantity}x " if self.quantity > 1 else ""
            b = discord.ui.Button(
                label=f"{qtxt}{it['name']} ({total:,} QP)"[:80],
                emoji=it.get("emoji") or None,
                style=discord.ButtonStyle.primary,
                row=1 + i // 2,
            )
            b.callback = self._buy_cb(iid)
            self.add_item(b)

    async def render(self) -> discord.Embed:
        bal = await self.cog.bot.scoring.get_balance(self.gid, self.uid)
        owned = {r["item_id"]: r["quantity"] for r in await self.cog.bot.contracts.inventory(self.gid, self.uid)}
        head = [f"Balance: {qp(bal)}"]
        if self.quantity > 1:
            head.append(f"**Bulk mode:** buying {self.quantity}x per tap")
        embed = discord.Embed(
            title="\N{CROSSED SWORDS} Autobattle Item Shop",
            description="\n".join(head)
            + "\n\nItems are spent each fight they're equipped for (in PvP, only the attacker's).",
            color=discord.Color.gold(),
        )
        for iid, it in self._slice():
            have = owned.get(iid, 0)
            emoji = it.get("emoji") or "\N{PACKAGE}"
            suffix = f"  \N{MIDDLE DOT}  owned: {have}" if have else ""
            embed.add_field(
                name=f"{emoji} {it['name']} - {int(it.get('price', 0)):,} QP{suffix}",
                value=it.get("description", ""),
                inline=False,
            )
        embed.set_footer(text=f"Page {self.page + 1}/{self.pages}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.uid:
            await interaction.response.send_message("This shop isn't yours.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._build_buttons()
        await interaction.response.edit_message(embed=await self.render(), view=self)

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self._refresh(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        await self._refresh(interaction)

    def _buy_cb(self, item_id: str):
        async def cb(interaction: discord.Interaction) -> None:
            it = items.get_item(item_id)
            total = int(it.get("price", 0)) * self.quantity
            bal = await self.cog.bot.scoring.get_balance(self.gid, self.uid)
            if bal < total:
                return await interaction.response.send_message(
                    f"You need {qp(total)} for {self.quantity}x {it['name']}; you have {qp(bal)}.",
                    ephemeral=True,
                )
            await self.cog.bot.scoring.sub_qp(self.gid, self.uid, total)
            await self.cog.bot.contracts.add_item(self.gid, self.uid, item_id, self.quantity)
            await self._refresh(interaction)

        return cb


class LogPager(discord.ui.View):
    """The paginated battle-log field on a result embed (ported from the legacy BattleLogView):
    the team status + rewards stay fixed and the log flips 8 entries at a time. Public -- anyone
    can page it, not just the fighter."""

    ENTRIES_PER_PAGE = 8

    def __init__(self, render, battle_log: "list[str]") -> None:
        # render(log_name, log_text) -> a FRESH result embed with that log page as its last field.
        # Rebuilding from scratch each page (rather than copying + appending to one embed) keeps
        # the team status pinned at the top and guarantees the log replaces instead of piling up.
        super().__init__(timeout=600)
        self._render = render
        self.log = battle_log
        self.page = 0
        self.pages = max(1, (len(battle_log) + self.ENTRIES_PER_PAGE - 1) // self.ENTRIES_PER_PAGE)
        self._sync()

    def _sync(self) -> None:
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    def page_embed(self) -> discord.Embed:
        start = self.page * self.ENTRIES_PER_PAGE
        chunk = self.log[start:start + self.ENTRIES_PER_PAGE]
        text = "\n".join(chunk) if chunk else "No battle log."
        if len(text) > 1024:
            text = text[:1021] + "..."
        return self._render(f"Battle Log (Page {self.page + 1}/{self.pages})", text)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.page_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.page_embed(), view=self)


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

    async def _team_embed(self, member, gid, uid, servant_ids, levels) -> discord.Embed:
        equips = await self.bot.contracts.equips(gid, uid)
        owned = {r["item_id"]: r["quantity"] for r in await self.bot.contracts.inventory(gid, uid)}
        lines = []
        for i, sid in enumerate(servant_ids, 1):
            s = self.bot.servants.get(sid)
            if s is None:
                lines.append(f"**{i}.** Servant #{sid} (unavailable)")
                continue
            lvl = levels.get(sid, 1)
            atk, hp = autobattle.battle_stats(s, lvl)
            iid = equips.get(sid)
            tag = _item_tag(iid)
            if tag:
                qty = owned.get(iid, 0)
                eq = f" -- {tag} (x{qty})" if qty > 0 else f" -- {tag} (out of stock)"
            else:
                eq = ""
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
            return await interaction.response.send_message(
                embed=await self._team_embed(interaction.user, gid, uid, team, levels), ephemeral=True
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
        await interaction.response.send_message(
            content="Autobattle team saved.",
            embed=await self._team_embed(interaction.user, gid, uid, chosen, levels),
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

    @ab.command(name="shop", description="Browse and buy consumable battle items with QP.")
    @app_commands.describe(quantity="Buy this many of each item per tap (default 1)")
    async def shop(self, interaction: discord.Interaction, quantity: "int | None" = 1) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        qty = max(1, min(int(quantity or 1), 99))
        view = ShopView(self, interaction.guild_id, interaction.user.id, qty)
        await interaction.response.send_message(embed=await view.render(), view=view, ephemeral=True)

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

    @staticmethod
    def _format_effects(c) -> str:
        """Active buff/debuff emotes for a servant (the status line), like the legacy."""
        icons = [engine.EFFECT_EMOJI.get(k, "✨") for k in c.get("active_buffs", {})]
        icons += [engine.EFFECT_EMOJI.get(k, "❌") for k in c.get("active_debuffs", {})]
        return " ".join(icons)

    def _team_status(self, combatants) -> str:
        """A team's status block (the legacy layout): alive/dead icon, class emote, name, level,
        active-effect emotes, then an HP bar -- shown at the TOP of the result embed."""
        blocks = []
        for c in combatants:
            icon = "✅" if c["alive"] else "💀"
            cur = max(0, c["current_hp"])
            effects = self._format_effects(c)
            eff = f" {effects}" if effects else ""
            blocks.append(
                f"{icon} {engine.class_emoji(c.get('className', ''))} **{c['name']}** Lv.{c['level']}{eff}\n"
                f"`{self._hp_bar(cur, c['max_hp'])}` {cur:,}/{c['max_hp']:,} HP"
            )
        return "\n\n".join(blocks) or "(none)"

    @staticmethod
    def _sprite_url(servant) -> "str | None":
        """The battle sprite for the composite: the command-card art (the same asset the legacy
        used) when we have it, else the battle figure (charaFigure), else the face. Custom units
        only have a face. NOTE: command cards populate after a servant-data resync; until then this
        falls back to the figure sprite (still a real sprite, not the face crop)."""
        if servant is None:
            return None
        for asset in (getattr(servant, "commands", None), servant.figure):
            if asset:
                return asset.get("1") or next(iter(asset.values()))
        return servant.face

    async def _sprites(self, session, servant_ids) -> "list[bytes]":
        out = []
        for sid in servant_ids:
            url = self._sprite_url(self.bot.servants.get(sid))
            if url:
                try:
                    out.append(await images.fetch_bytes(session, url))
                except Exception:  # a sprite that won't fetch just drops from the composite
                    pass
        return out

    async def _battle_image(self, bg_url, left_ids, right_ids) -> "discord.File | None":
        """Composite both teams' sprites over the battle background -> a discord.File (battle.png),
        or None on any fetch/decode failure (the image is cosmetic; the caller falls back)."""
        session = self.bot.http_session
        if not session or not bg_url:
            return None
        try:
            bg = await images.fetch_bytes(session, bg_url)
            png = images.battle_preview(
                bg, await self._sprites(session, left_ids), await self._sprites(session, right_ids)
            )
            return discord.File(io.BytesIO(png), filename="battle.png")
        except Exception:
            return None

    async def _consume_items(self, gid, uid, player_team, initial) -> "list[str]":
        """Spend one copy of each item that was equipped for the fight (win or lose, whether or not
        it triggered). The equip is a STICKY loadout: it stays set even at 0 stock -- it just isn't
        fielded until you restock (see _side) -- so players never have to re-equip. Display lines."""
        lines = []
        for c in player_team:
            was = initial.get(c["servant_id"])
            if not was:
                continue
            remaining = await self.bot.contracts.add_item(gid, uid, was, -1)
            tail = "out of stock, restock to use" if remaining <= 0 else f"{remaining} left"
            lines.append(f"{_item_tag(was)} ({tail})")
        return lines

    def _battle_embed(
        self, enc, state, item_lines, reward_line=None, has_image=False, log_name=None, log_text=None
    ) -> discord.Embed:
        """A fresh result embed (legacy layout): VICTORY/DEFEAT title, then team status with HP bars
        at the TOP, rewards, then the current battle-log page as the last field. Rebuilt per page by
        LogPager so the log replaces (never piles up) and the status stays pinned at the top."""
        won, draw = state["victory"], state.get("draw")
        if draw:
            color, title = discord.Color.gold(), "⚔️ DRAW!"
        elif won:
            color, title = discord.Color.green(), "🎉 VICTORY!"
        else:
            color, title = discord.Color.red(), "💔 DEFEAT"
        embed = discord.Embed(
            title=title, description=f"Battle against **{enc.get('name', 'Stage')}**", color=color
        )
        if has_image:
            embed.set_image(url="attachment://battle.png")
        elif enc.get("bg_image"):
            embed.set_image(url=enc["bg_image"])
        embed.add_field(name="Your Team", value=self._team_status(state["player_servants"]), inline=True)
        embed.add_field(name="Enemy Team", value=self._team_status(state["enemy_servants"]), inline=True)
        if reward_line:
            embed.add_field(name="Reward", value=reward_line, inline=False)
        if item_lines:
            embed.add_field(name="Items used", value="\n".join(item_lines), inline=False)
        if log_name is not None:
            embed.add_field(name=log_name, value=log_text or "No battle log.", inline=False)
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

        def render(log_name, log_text):
            return self._battle_embed(
                enc, state, item_lines, reward_line,
                has_image=battle_file is not None, log_name=log_name, log_text=log_text,
            )

        pager = LogPager(render, state["battle_log"])
        send_kwargs = {"file": battle_file or discord.utils.MISSING}
        if pager.pages > 1:
            send_kwargs["view"] = pager
        await interaction.followup.send(embed=pager.page_embed(), **send_kwargs)

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
        self, challenger, opponent, state, winner, item_lines, reward_line,
        bg_url=None, has_image=False, log_name=None, log_text=None,
    ) -> discord.Embed:
        """A fresh PvP result embed: winner title, both players' team status (HP bars) at the top,
        rewards, then the current battle-log page as the last field (rebuilt per page by LogPager)."""
        draw = state.get("draw")
        if draw:
            color, title = discord.Color.gold(), "⚔️ DRAW!"
        elif winner is not None and winner.id == challenger.id:
            color, title = discord.Color.green(), f"🎉 {challenger.display_name} wins!"
        else:
            color, title = discord.Color.red(), f"🎉 {opponent.display_name} wins!"
        embed = discord.Embed(
            title=title,
            description=f"**{challenger.display_name}** vs **{opponent.display_name}**",
            color=color,
        )
        if has_image:
            embed.set_image(url="attachment://battle.png")
        elif bg_url:
            embed.set_image(url=bg_url)
        embed.add_field(name=challenger.display_name, value=self._team_status(state["player_servants"]), inline=True)
        embed.add_field(name=opponent.display_name, value=self._team_status(state["enemy_servants"]), inline=True)
        if reward_line:
            embed.add_field(name="Reward", value=reward_line, inline=False)
        if item_lines:
            embed.add_field(
                name=f"{challenger.display_name}'s items used", value="\n".join(item_lines), inline=False
            )
        if log_name is not None:
            embed.add_field(name=log_name, value=log_text or "No battle log.", inline=False)
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

        def render(log_name, log_text):
            return self._pvp_embed(
                interaction.user, opponent, state, winner, item_lines, reward_line,
                bg_url=bg_url, has_image=battle_file is not None,
                log_name=log_name, log_text=log_text,
            )

        pager = LogPager(render, state["battle_log"])
        send_kwargs = {
            "file": battle_file or discord.utils.MISSING,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if pager.pages > 1:
            send_kwargs["view"] = pager
        await interaction.followup.send(embed=pager.page_embed(), **send_kwargs)


async def setup(bot) -> None:
    await bot.add_cog(Autobattle(bot))
