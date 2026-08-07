"""Scheduled SQLite -> S3 backups. Loaded only when BACKUP_S3_BUCKET is configured (see
bot.setup_hook). A periodic tasks.loop takes a backup on an interval, plus /dbbackup lets a mod
trigger one on demand (e.g. right before a risky migration or data edit)."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from permissions import is_mod

log = logging.getLogger("holmesbot.backup")


class BackupCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.service = bot.backups  # BackupService, attached in setup_hook before loading this cog
        self._auto.change_interval(hours=max(1, bot.config.backup_interval_hours))
        self._auto.start()

    def cog_unload(self) -> None:
        self._auto.cancel()

    @tasks.loop(hours=6)  # interval overridden from config in __init__
    async def _auto(self) -> None:
        try:
            await self.service.run_once()
        except Exception:
            # A failed backup must never take the bot down; log and try again next tick.
            log.exception("scheduled db backup failed")

    @_auto.before_loop
    async def _before_auto(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="dbbackup", description="(Mods) Back up the database to S3 right now.")
    @app_commands.guild_only()
    async def dbbackup(self, interaction: discord.Interaction) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to run a backup.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            key = await self.service.run_once()
        except Exception as exc:
            log.exception("manual db backup failed")
            return await interaction.followup.send(
                f"Backup failed: {type(exc).__name__}. Check the bot logs.", ephemeral=True
            )
        await interaction.followup.send(f"Backup uploaded: `{key}`", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(BackupCog(bot))
