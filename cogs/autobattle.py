"""Autobattle cog (experimental). Loaded only when AUTOBATTLE_ENABLED is set (see bot.py), so
it ships dark. Phase 4 adds team config; the /autobattle command + economy come in Phase 5.

Uses the contract feature's roster (servant_contracts) for owned servants, so it shares the
contract-access gate -- a player needs the contract feature to have servants to field.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import autobattle
from data.servants import class_display

_DENY = "The autobattle feature isn't open to you yet."


class Autobattle(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    def _allowed(self, user_id: int) -> bool:
        return self.bot.config.contract_open or user_id in self.bot.config.contract_whitelist

    async def _owned_levels(self, guild_id: int, user_id: int) -> "dict[int, int]":
        return {
            r["servant_id"]: r["level"]
            for r in await self.bot.contracts.owned(guild_id, user_id)
        }

    def _team_embed(self, member, servant_ids: "list[int]", levels: "dict[int, int]") -> discord.Embed:
        lines = []
        for i, sid in enumerate(servant_ids, 1):
            s = self.bot.servants.get(sid)
            if s is None:
                lines.append(f"**{i}.** Servant #{sid} (unavailable)")
                continue
            lvl = levels.get(sid, 1)
            atk, hp = autobattle.battle_stats(s, lvl)
            lines.append(
                f"**{i}.** {s.name} ({class_display(s.class_name) or '?'}) "
                f"Lv {lvl} -- {atk:,} ATK / {hp:,} HP"
            )
        return discord.Embed(
            title=f"{member.display_name}'s Autobattle Team",
            description="\n".join(lines) or "(empty)",
            color=discord.Color.blurple(),
        )

    @app_commands.command(
        name="autobattleteam",
        description="Set or view your autobattle team (1-3 of your servants, front to back).",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        first="Front servant (leave all blank to view your current team)",
        second="Middle servant",
        third="Back servant",
    )
    async def autobattleteam(
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

        if not picks:  # view current team
            team = await self.bot.contracts.battle_team(gid, uid)
            if not team:
                return await interaction.response.send_message(
                    "You have no autobattle team yet. Set one with "
                    "/autobattleteam first:<servant> (up to three).",
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                embed=self._team_embed(interaction.user, team, levels), ephemeral=True
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
            embed=self._team_embed(interaction.user, chosen, levels),
            ephemeral=True,
        )

    async def _team_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> "list[app_commands.Choice[int]]":
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

    @autobattleteam.autocomplete("first")
    async def _ac_first(self, interaction, current):
        return await self._team_autocomplete(interaction, current)

    @autobattleteam.autocomplete("second")
    async def _ac_second(self, interaction, current):
        return await self._team_autocomplete(interaction, current)

    @autobattleteam.autocomplete("third")
    async def _ac_third(self, interaction, current):
        return await self._team_autocomplete(interaction, current)


async def setup(bot) -> None:
    await bot.add_cog(Autobattle(bot))
