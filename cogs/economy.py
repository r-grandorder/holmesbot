from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from branding import MAX_QP, parse_qp, qp

_NO_PINGS = discord.AllowedMentions.none()


class Economy(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Top QP earners in this server.")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.scoring.leaderboard(interaction.guild_id, 10)
        if not rows:
            await interaction.response.send_message("No QP earned yet. Play a round.", ephemeral=True)
            return
        lines = [
            f"**{i}.** <@{r['user_id']}> — {qp(r['points'])} ({r['wins']} wins)"
            for i, r in enumerate(rows, start=1)
        ]
        embed = discord.Embed(title="QP Leaderboard", description="\n".join(lines))
        await interaction.response.send_message(embed=embed, allowed_mentions=_NO_PINGS)

    @app_commands.command(name="qp", description="Check a QP balance.")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose balance to check (default yourself)")
    async def qp(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        balance = await self.bot.scoring.get_balance(interaction.guild_id, target.id)
        earned = await self.bot.scoring.get_earned(interaction.guild_id, target.id)
        await interaction.response.send_message(
            f"{target.display_name} has {qp(balance)} (earned {qp(earned)} all-time).",
            ephemeral=True,
        )

    @app_commands.command(name="pay", description="Send QP to another player.")
    @app_commands.guild_only()
    @app_commands.describe(member="Who to pay", amount="How much (e.g. 500, 1k, 3.2m)")
    async def pay(
        self, interaction: discord.Interaction, member: discord.Member, amount: str
    ) -> None:
        n = parse_qp(amount)
        if n is None or n < 1:
            await interaction.response.send_message(
                "Enter a valid amount (e.g. 500, 1k, 3.2m).", ephemeral=True
            )
            return
        if member.bot or member.id == interaction.user.id:
            await interaction.response.send_message("Pick another player to pay.", ephemeral=True)
            return
        status = await self.bot.scoring.transfer(
            interaction.guild_id, interaction.user.id, member.id, n
        )
        if status == "insufficient":
            have = await self.bot.scoring.get_balance(interaction.guild_id, interaction.user.id)
            await interaction.response.send_message(
                f"Not enough QP. You have {qp(have)}.", ephemeral=True
            )
            return
        if status == "overflow":
            await interaction.response.send_message(
                f"That would put them over the {qp(MAX_QP)} cap.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"{interaction.user.mention} sent {qp(n)} to {member.mention}.",
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )


async def setup(bot) -> None:
    await bot.add_cog(Economy(bot))
