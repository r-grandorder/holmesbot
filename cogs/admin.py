from __future__ import annotations

import pathlib
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from branding import parse_qp, qp
from data.stimmy_hosts import STIMMY_HOSTS
from permissions import is_mod

_STIMMY_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "stimmy"


def _stimmy_file(image: str) -> discord.File:
    """A fresh (single-use) File for a stimmy host's transparent portrait."""
    return discord.File(str(_STIMMY_DIR / image), filename="stimmy.png")

_GAME_CHOICES = [
    app_commands.Choice(name="guess_servant", value="guess_servant"),
    app_commands.Choice(name="guess_shadow", value="guess_shadow"),
    app_commands.Choice(name="guess_audio", value="guess_audio"),
    app_commands.Choice(name="guess_skill", value="guess_skill"),
]


class Admin(commands.Cog):
    """Staff-only configuration. Every command is gated on moderator permissions
    OR being the bot owner (the application owner always passes)."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        if is_mod(interaction.user):
            return True
        await interaction.response.send_message(
            "You need moderator permissions (or be the bot owner) to use this.",
            ephemeral=True,
        )
        return False

    restrict = app_commands.Group(
        name="restrict",
        description="Manage restricted servant art (content policy)",
        guild_only=True,
    )
    gameconfig = app_commands.Group(
        name="gameconfig", description="Game configuration", guild_only=True
    )
    alias = app_commands.Group(
        name="alias", description="Manage accepted servant name aliases", guild_only=True
    )
    summonconfig = app_commands.Group(
        name="summonconfig",
        description="Summon feature configuration",
        guild_only=True,
    )

    # --- restrictions ---
    @restrict.command(name="add", description="Restrict a servant or specific ascensions.")
    @app_commands.describe(
        servant_id="Atlas Academy servant ID",
        scope="full = whole servant; ascension/costume = specific art",
        ascensions="comma-separated ascension keys (required unless scope is full)",
        reason="why it's restricted",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="full", value="full"),
            app_commands.Choice(name="ascension", value="ascension"),
            app_commands.Choice(name="costume", value="costume"),
        ]
    )
    async def restrict_add(
        self,
        interaction: discord.Interaction,
        servant_id: int,
        scope: app_commands.Choice[str],
        ascensions: str = "",
        reason: str | None = None,
    ) -> None:
        keys = [k.strip() for k in ascensions.split(",") if k.strip()]
        if scope.value != "full" and not keys:
            await interaction.response.send_message(
                "Provide ascension keys for that scope.", ephemeral=True
            )
            return
        rule_id = await self.bot.restrictions.add(
            servant_id, scope.value, keys, reason, interaction.user.id
        )
        servant = self.bot.servants.get(servant_id)
        label = servant.name if servant else f"ID {servant_id}"
        await interaction.response.send_message(
            f"Restricted {label} (rule #{rule_id}, scope {scope.value}).", ephemeral=True
        )

    @restrict.command(name="remove", description="Remove a restriction rule by ID.")
    async def restrict_remove(self, interaction: discord.Interaction, rule_id: int) -> None:
        ok = await self.bot.restrictions.remove(rule_id)
        await interaction.response.send_message(
            "Removed." if ok else "No such rule.", ephemeral=True
        )

    @restrict.command(name="list", description="List restriction rules.")
    async def restrict_list(self, interaction: discord.Interaction) -> None:
        rules = await self.bot.restrictions.list_all()
        if not rules:
            await interaction.response.send_message("No restrictions set.", ephemeral=True)
            return
        lines = []
        for r in rules:
            servant = self.bot.servants.get(r["servant_id"])
            name = servant.name if servant else f"ID {r['servant_id']}"
            extra = f" {list(r['ascension_keys'])}" if r["ascension_keys"] else ""
            lines.append(f"#{r['id']} {name} [{r['scope']}{extra}]")
        await interaction.response.send_message("\n".join(lines[:50]), ephemeral=True)

    # --- QP admin ---
    @app_commands.command(name="qp_set", description="Set a member's QP balance.")
    @app_commands.guild_only()
    @app_commands.describe(member="Member", amount="Amount (e.g. 500, 1k, 3.2b)")
    async def qp_set(
        self, interaction: discord.Interaction, member: discord.Member, amount: str
    ) -> None:
        n = parse_qp(amount)
        if n is None:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return
        new = await self.bot.scoring.set_balance(interaction.guild_id, member.id, n)
        await interaction.response.send_message(
            f"Set {member.display_name} to {qp(new)}.", ephemeral=True
        )

    @app_commands.command(name="qp_add", description="Add QP to a member's balance.")
    @app_commands.guild_only()
    @app_commands.describe(member="Member", amount="Amount (e.g. 500, 1k, 3.2b)")
    async def qp_add(
        self, interaction: discord.Interaction, member: discord.Member, amount: str
    ) -> None:
        n = parse_qp(amount)
        if n is None or n < 1:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return
        new = await self.bot.scoring.add_qp(interaction.guild_id, member.id, n)
        await interaction.response.send_message(
            f"Added {qp(n)}. {member.display_name} now has {qp(new)}.", ephemeral=True
        )

    @app_commands.command(name="qp_sub", description="Subtract QP from a member's balance.")
    @app_commands.guild_only()
    @app_commands.describe(member="Member", amount="Amount (e.g. 500, 1k, 3.2b)")
    async def qp_sub(
        self, interaction: discord.Interaction, member: discord.Member, amount: str
    ) -> None:
        n = parse_qp(amount)
        if n is None or n < 1:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return
        new = await self.bot.scoring.sub_qp(interaction.guild_id, member.id, n)
        await interaction.response.send_message(
            f"Subtracted {qp(n)}. {member.display_name} now has {qp(new)}.", ephemeral=True
        )

    @app_commands.command(name="stimmy", description="Post a QP stimulus that members can each claim once.")
    @app_commands.guild_only()
    @app_commands.describe(
        amount="QP each person receives (e.g. 500, 1k)",
        duration="Seconds the stimulus stays open (default 300, max 86400)",
    )
    async def stimmy(
        self, interaction: discord.Interaction, amount: str, duration: int = 300
    ) -> None:
        n = parse_qp(amount)
        if n is None or n < 1:
            return await interaction.response.send_message("Invalid amount.", ephemeral=True)
        if not 1 <= duration <= 86400:
            return await interaction.response.send_message(
                "Duration must be between 1 and 86400 seconds (24 hours).", ephemeral=True
            )
        view = StimulusView(self.bot, n, duration)
        await interaction.response.send_message(
            embed=view.render(), view=view, file=_stimmy_file(view.host["image"])
        )
        view.message = await interaction.original_response()

    @app_commands.command(name="qp_reset", description="Wipe all QP and scores in this server.")
    @app_commands.guild_only()
    async def qp_reset(self, interaction: discord.Interaction) -> None:
        await self.bot.scoring.reset_guild(interaction.guild_id)
        await interaction.response.send_message(
            "Wiped all QP and scores for this server.", ephemeral=True
        )

    @app_commands.command(
        name="forfeit", description="End the current round in this channel and reveal the answer."
    )
    @app_commands.guild_only()
    async def forfeit(self, interaction: discord.Interaction) -> None:
        round_ = self.bot.active_rounds.get(interaction.channel_id)
        if round_ is None or round_.claimed:
            await interaction.response.send_message(
                "No round is running in this channel.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Round forfeited; revealing the answer.", ephemeral=True
        )
        await round_.forfeit(interaction.channel)

    # --- game config ---
    @gameconfig.command(name="toggle", description="Enable or disable a game.")
    @app_commands.choices(game=_GAME_CHOICES)
    async def gameconfig_toggle(
        self, interaction: discord.Interaction, game: app_commands.Choice[str], enabled: bool
    ) -> None:
        await self.bot.guild_config.set_game_enabled(interaction.guild_id, game.value, enabled)
        await interaction.response.send_message(
            f"{game.value}: {'on' if enabled else 'off'}.", ephemeral=True
        )

    @gameconfig.command(name="channel", description="Restrict games to specific channels.")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="clear", value="clear"),
        ]
    )
    async def gameconfig_channel(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if action.value == "clear":
            await self.bot.guild_config.clear_channels(interaction.guild_id)
            await interaction.response.send_message(
                "Games are allowed in all channels now.", ephemeral=True
            )
            return
        if channel is None:
            await interaction.response.send_message("Pick a channel.", ephemeral=True)
            return
        if action.value == "add":
            await self.bot.guild_config.add_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(
                f"Games allowed in {channel.mention}.", ephemeral=True
            )
        else:
            await self.bot.guild_config.remove_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(
                f"Removed {channel.mention}.", ephemeral=True
            )

    @gameconfig.command(
        name="logchannel", description="Set or clear the game audit-log channel."
    )
    @app_commands.describe(channel="Channel for started-game logs (omit to disable)")
    async def gameconfig_logchannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if channel is None:
            await self.bot.guild_config.set_log_channel(interaction.guild_id, None)
            await interaction.response.send_message("Game logging disabled.", ephemeral=True)
            return
        try:
            await channel.send("Game logs (answers + media) will post here.")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I can't post in {channel.mention}. Give my role View Channel + Send "
                "Messages + Embed Links + Attach Files there, then run this again.",
                ephemeral=True,
            )
            return
        await self.bot.guild_config.set_log_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"Game logs (answers + media) will post to {channel.mention}.", ephemeral=True
        )

    # --- contracted-servant feature config ---
    @summonconfig.command(
        name="grailchannel", description="Restrict grail drops to specific channels."
    )
    @app_commands.describe(channel="Channel to add or remove (not needed for clear/list)")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="clear", value="clear"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    async def summonconfig_grailchannel(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if action.value == "clear":
            await self.bot.guild_config.clear_grail_channels(interaction.guild_id)
            await interaction.response.send_message(
                "Grail drops can spawn in any channel now.", ephemeral=True
            )
            return
        if action.value == "list":
            chans = await self.bot.guild_config.grail_channels(interaction.guild_id)
            msg = (
                "Grail drops are limited to: " + ", ".join(f"<#{c}>" for c in chans)
                if chans
                else "Grail drops can spawn in any channel."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return
        if channel is None:
            await interaction.response.send_message("Pick a channel.", ephemeral=True)
            return
        if action.value == "add":
            await self.bot.guild_config.add_grail_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(
                f"Grail drops can spawn in {channel.mention}.", ephemeral=True
            )
        else:
            await self.bot.guild_config.remove_grail_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(
                f"Grail drops removed from {channel.mention}.", ephemeral=True
            )

    @summonconfig.command(
        name="announcechannel",
        description="Channel for contract announcements, shares, and level-ups (omit to clear).",
    )
    @app_commands.describe(channel="Where contract broadcasts post (omit to post in-context)")
    async def summonconfig_announcechannel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        if channel is None:
            await self.bot.guild_config.set_announce_channel(interaction.guild_id, None)
            await interaction.response.send_message(
                "Contract announcements will post in-context now.", ephemeral=True
            )
            return
        try:
            await channel.send("Contract announcements (summons, level-ups) will post here.")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I can't post in {channel.mention}. Give my role View Channel + Send Messages "
                "+ Embed Links there, then run this again.",
                ephemeral=True,
            )
            return
        await self.bot.guild_config.set_announce_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"Contract announcements will post to {channel.mention}.", ephemeral=True
        )

    # --- aliases (extra accepted names per servant; handles Atlas naming quirks) ---
    @alias.command(name="add", description="Add an accepted name for a servant.")
    @app_commands.describe(servant="Search by name", alias="The accepted name to add")
    async def alias_add(
        self, interaction: discord.Interaction, servant: int, alias: str
    ) -> None:
        ok = await self.bot.aliases.add(servant, alias, interaction.user.id)
        if not ok:
            await interaction.response.send_message("That alias is empty.", ephemeral=True)
            return
        s = self.bot.servants.get(servant)
        await interaction.response.send_message(
            f"Added alias **{alias}** for {s.name if s else f'ID {servant}'}.", ephemeral=True
        )

    @alias.command(name="remove", description="Remove an alias by its ID.")
    async def alias_remove(self, interaction: discord.Interaction, alias_id: int) -> None:
        ok = await self.bot.aliases.remove(alias_id)
        await interaction.response.send_message(
            "Removed." if ok else "No such alias.", ephemeral=True
        )

    @alias.command(name="list", description="List a servant's aliases.")
    @app_commands.describe(servant="Search by name")
    async def alias_list(self, interaction: discord.Interaction, servant: int) -> None:
        rows = await self.bot.aliases.list_for(servant)
        s = self.bot.servants.get(servant)
        label = s.name if s else f"ID {servant}"
        if not rows:
            await interaction.response.send_message(f"No aliases for {label}.", ephemeral=True)
            return
        lines = [f"#{r['id']} {r['alias']}" for r in rows]
        await interaction.response.send_message(
            f"**{label}**\n" + "\n".join(lines[:50]), ephemeral=True
        )

    def _servant_choices(self, current: str) -> list[app_commands.Choice[int]]:
        return [
            app_commands.Choice(name=f"{s.name[:90]} (#{s.id})", value=s.id)
            for s in self.bot.servants.search(current, 25)
        ]

    @alias_add.autocomplete("servant")
    async def _alias_add_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return self._servant_choices(current)

    @alias_list.autocomplete("servant")
    async def _alias_list_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return self._servant_choices(current)


class StimulusView(discord.ui.View):
    """A QP stimulus (created by /stimmy), hosted by a random wealth-themed servant: any member
    claims a flat amount once and gets a fleeting public shout-out, then it self-deletes when
    the window closes. Claimers are tracked in memory, so a mid-stimulus restart just ends it
    early (same trade-off as the grail-drop views)."""

    def __init__(self, bot, amount: int, duration: int) -> None:
        super().__init__(timeout=float(duration))
        self.bot = bot
        self.amount = amount
        self.expires = time.time() + duration
        self.host = random.choice(list(STIMMY_HOSTS.values()))
        self.line = random.choice(self.host["lines"])
        self.claimers: set[int] = set()
        self.message: discord.Message | None = None

    def render(self) -> discord.Embed:
        embed = discord.Embed(
            title="Stimulus Available",
            description=(
                f"**{self.host['name']}:** *\"{self.line}\"*\n\n"
                f"Click **Claim QP** to receive {qp(self.amount)}.\n"
                f"One claim per person. Closes <t:{int(self.expires)}:R>."
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url="attachment://stimmy.png")
        return embed

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Claim QP", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id in self.claimers:
            return await interaction.response.send_message(
                "You already claimed this stimulus.", ephemeral=True
            )
        self.claimers.add(interaction.user.id)  # before any await: callbacks run serially
        new = await self.bot.scoring.add_qp(interaction.guild_id, interaction.user.id, self.amount)
        await interaction.response.send_message(
            f"You claimed {qp(self.amount)}. Balance: {qp(new)}.", ephemeral=True
        )
        try:  # fleeting public shout-out; self-deletes, and the mention doesn't ping
            await interaction.channel.send(
                f"{interaction.user.mention} claimed {qp(self.amount)}!",
                delete_after=8,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))
