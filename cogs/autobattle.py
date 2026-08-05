"""Autobattle cog (experimental). Loaded only when AUTOBATTLE_ENABLED is set (see bot.py), so
it ships dark.

/autobattleteam configures a 1-3 servant team; /autobattle fights a preconfigured PvE stage with
it. Battling is FREE -- winning rewards XP to the whole team. The QP sink lives in items (bought
with QP, equipped to power the team), not in an entry fee.

Uses the contract feature's roster (servant_contracts) for owned servants, so it shares the
contract-access gate -- a player needs the contract feature to have servants to field.
"""
from __future__ import annotations

import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from data import autobattle
from data import autobattle_engine as engine
from data.servants import class_display

_DENY = "The autobattle feature isn't open to you yet."
# Seconds between battles per user: anti-spam + a light rate-limit on XP farming. Tunable.
AUTOBATTLE_COOLDOWN = 20
_DIFFICULTY_ORDER = {"beginner": 0, "intermediate": 1, "hard": 2}


class Autobattle(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}

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

    # --- /autobattle -----------------------------------------------------------------------------

    def _build_team(self, id_levels: "list[tuple[int, int]]") -> "list[dict]":
        """[(servant_id, level), ...] -> combatants (front-to-back), skipping any servant missing
        from the index. Kits come from the baked KitIndex when present, else the unit is vanilla."""
        team: list[dict] = []
        pos = 0
        for sid, lvl in id_levels:
            s = self.bot.servants.get(sid)
            if s is None:
                continue
            kit = self.bot.kits.get(sid) if self.bot.kits else None
            team.append(engine.combatant(s, max(1, lvl), pos, kit=kit))
            pos += 1
        return team

    @staticmethod
    def _trim_log(lines: "list[str]", head: int = 6, tail: int = 26, limit: int = 1800) -> str:
        """Compact the log: keep the opening + the decisive end, collapse the middle of long
        fights, then hard-cap by length so it fits inside an embed code block."""
        if len(lines) > head + tail:
            skipped = len(lines) - head - tail
            lines = lines[:head] + [f"... ({skipped} more lines) ..."] + lines[-tail:]
        text = "\n".join(lines)
        if len(text) > limit:
            text = text[-limit:]
            nl = text.find("\n")
            text = "..." + text[nl:] if nl != -1 else text
        return text

    def _battle_embed(self, member, enc: dict, state: dict, xp_lines: "list[str]") -> discord.Embed:
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
        return embed

    @app_commands.command(
        name="autobattle",
        description="Fight a PvE stage with your team. Free -- win to earn XP for your servants.",
    )
    @app_commands.guild_only()
    @app_commands.describe(stage="Which stage to fight (leave blank for a random one)")
    async def autobattle_cmd(
        self, interaction: discord.Interaction, stage: "str | None" = None
    ) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id

        team_ids = await self.bot.contracts.battle_team(gid, uid)
        if not team_ids:
            return await interaction.response.send_message(
                "Set a team first with /autobattleteam.", ephemeral=True
            )
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
        player_team = self._build_team([(sid, levels.get(sid, 1)) for sid in team_ids])
        if not player_team:
            return await interaction.followup.send("Your team's servants are unavailable.")
        enemy_team = self._build_team(
            [(m["servant_id"], m["level"]) for m in enc.get("servants", [])]
        )
        if not enemy_team:
            return await interaction.followup.send("This stage is misconfigured (no enemies).")

        state = engine.build_state(player_team, enemy_team)
        engine.resolve(state)

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
            embed=self._battle_embed(interaction.user, enc, state, xp_lines)
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

    @autobattle_cmd.autocomplete("stage")
    async def _ac_stage(self, interaction, current):
        return await self._stage_autocomplete(interaction, current)


async def setup(bot) -> None:
    await bot.add_cog(Autobattle(bot))
