"""The garden: /water someone to grow them, /mulch them to boost what they gain.

A deliberately low-effort way to earn QP and be a bit silly. Ported in spirit from the legacy
bot's /water, minus its skill trees and weather.

The two cooldowns are the whole design:
  * growth sits on the person being WATERED, so a popular target doesn't shoot up
  * the QP payout sits on the WATERER, so income is a fixed rate no matter who you water

Watering someone on their growth cooldown still counts and still pays -- it just doesn't grow
them. The reply is public (that's the fun of it) but never pings the person being watered.
"""
from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from branding import qp
from data import garden

_NO_PINGS = discord.AllowedMentions.none()


def _rel(when) -> str:
    """A Discord relative timestamp for a stored 'YYYY-MM-DD HH:MM:SS' UTC value."""
    if isinstance(when, str):
        when = dt.datetime.strptime(when, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return f"<t:{int(when.timestamp())}:R>"


class Garden(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    # ---- /water ----
    @app_commands.command(
        name="water", description="Water another player to help them grow. Earns QP twice a day."
    )
    @app_commands.guild_only()
    @app_commands.describe(user="Who to water")
    async def water(self, interaction: discord.Interaction, user: discord.Member) -> None:
        me = self.bot.user
        is_bot_target = me is not None and user.id == me.id
        if user.id == interaction.user.id:
            return await interaction.response.send_message(
                "You cannot water yourself. That is not how gardening works.", ephemeral=True
            )
        if user.bot and not is_bot_target:
            return await interaction.response.send_message(
                "Other bots do not care for water. Try a human.", ephemeral=True
            )

        gid = interaction.guild_id
        cfg = self.bot.config
        res = await self.bot.garden.water(
            gid, interaction.user.id, user.id, qp_cooldown_hours=cfg.water_qp_cooldown_hours
        )
        reward = 0
        if res["reward_due"] and cfg.water_qp_reward > 0:
            # add_qp moves the spendable balance only, so watering never inflates the lifetime
            # QP leaderboard -- that stays a record of guessing.
            await self.bot.scoring.add_qp(gid, interaction.user.id, cfg.water_qp_reward)
            reward = cfg.water_qp_reward

        grew = res["grew"]
        line = garden.water_line(user.display_name, grew)
        # Watering the bot gets Holmes reacting to it before the usual growth line.
        desc = f'*"{garden.holmes_line()}"*\n\n{line}' if is_bot_target else line
        embed = discord.Embed(
            description=desc,
            color=discord.Color.green() if grew else discord.Color.greyple(),
        )
        # The person being watered, whoever that is -- which covers the bot case too.
        embed.set_thumbnail(url=user.display_avatar.url)
        if is_bot_target:
            embed.title = "You water the detective"
        if grew:
            embed.add_field(name="Growth", value=f"+{garden.format_height(res['growth_mm'])}")
        elif res["next_growth_at"]:
            # Refused for being too recently watered: say exactly when they're ready again.
            embed.add_field(name="Ready again", value=_rel(res["next_growth_at"]))
        embed.add_field(name="Height", value=garden.format_height(res["height_mm"]))
        if res["multiplier"] > 1.0 and res["mulch_expires"]:
            embed.add_field(
                name=garden.MULCH_NAME,
                value=f"{res['multiplier']:g}x growth, ends {_rel(res['mulch_expires'])}",
                inline=False,
            )
        if reward:
            embed.add_field(name="Reward", value=f"+{qp(reward)}", inline=False)
        n = res["times_watered"]
        embed.set_footer(text=f"Watered {n} time{'' if n == 1 else 's'}")
        # Public so everyone sees it, but the watered player is never pinged.
        await interaction.response.send_message(embed=embed, allowed_mentions=_NO_PINGS)

    # ---- /mulch ----
    @app_commands.command(
        name="mulch",
        description=f"Spend a {garden.MULCH_NAME} to boost another player's growth for a while.",
    )
    @app_commands.guild_only()
    @app_commands.describe(user="Who to mulch")
    async def mulch(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if user.bot and not (self.bot.user and user.id == self.bot.user.id):
            return await interaction.response.send_message(
                "Other bots do not care for fertilizer. Try a human.", ephemeral=True
            )
        gid = interaction.guild_id
        eff = await self.bot.garden.apply_mulch(
            gid, interaction.user.id, user.id,
            multiplier=garden.MULCH_MULTIPLIER, hours=garden.MULCH_HOURS,
        )
        if eff is None:
            return await interaction.response.send_message(
                f"You have no {garden.MULCH_NAME}. Buy some in Da Vinci's Workshop (/shop).",
                ephemeral=True,
            )
        whose = "your own soil" if user.id == interaction.user.id else f"{user.display_name}'s soil"
        embed = discord.Embed(
            title=f"{garden.MULCH_NAME} applied",
            description=(
                f"{interaction.user.display_name} works fertilizer into {whose}. "
                f"Watering now grows them **{eff['multiplier']:g}x** until {_rel(eff['expires_at'])}."
            ),
            color=discord.Color.dark_gold(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"{eff['remaining']} {garden.MULCH_NAME} left")
        await interaction.response.send_message(embed=embed, allowed_mentions=_NO_PINGS)

    # ---- /height ----
    @app_commands.command(name="height", description="Check how tall someone has grown.")
    @app_commands.guild_only()
    @app_commands.describe(user="Whose height to check (defaults to you)")
    async def height(
        self, interaction: discord.Interaction, user: discord.Member | None = None
    ) -> None:
        target = user or interaction.user
        gid = interaction.guild_id
        info = await self.bot.garden.height(gid, target.id)
        times = info["times_watered"]
        mulch = await self.bot.garden.active_mulch(gid, target.id)
        line = (f"**{target.display_name}** stands **{garden.format_height(info['height_mm'])}** "
                f"tall after {times} watering{'' if times == 1 else 's'}.")
        line += (f"\nCan be watered again {_rel(info['next_growth_at'])}."
                 if info["next_growth_at"] else "\nReady to be watered now.")
        if mulch:
            line += (f"\n{garden.MULCH_NAME} active: **{mulch['multiplier']:g}x** growth, "
                     f"ends {_rel(mulch['expires_at'])}.")
        await interaction.response.send_message(line, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Garden(bot))
