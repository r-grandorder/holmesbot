from __future__ import annotations

import asyncio
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from data import contract_game
from data import halloween as H
from permissions import is_mod
from services.halloween import HalloweenService

log = logging.getLogger(__name__)
_DENY = "The Halloween event isn't open to you yet."
_EVENT_CHOICES = [
    app_commands.Choice(name="Trick or Treat", value="trick_or_treat"),
    app_commands.Choice(name="Candy Spill", value="candy_spill"),
]


class HalloweenCog(commands.Cog):
    """Loaded only with both contracts and HALLOWEEN_WHITELIST enabled."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self.service = HalloweenService(bot.db.pool)
        self._channels: set[int] = set()
        self._views: set[CandyEventView] = set()

    def allowed(self, user_id: int) -> bool:
        return self.bot.config.halloween_allowed(user_id)

    async def cog_unload(self) -> None:
        for view in tuple(self._views):
            await view.on_timeout()

    async def _face(self, servant) -> str | None:
        if servant is None or not servant.face:
            return None
        try:
            allow = await self.bot.restrictions.build_allow()
            # The cached face is the first ascension. Respect that exact art restriction too.
            if allow(servant.id, "1") and contract_game.display_art(servant, allow):
                return servant.face
        except Exception:
            # Claims may already have committed. Report the result even when the
            # optional portrait cannot be checked, without exposing unchecked art.
            log.warning("Couldn't verify Halloween portrait permissions for servant %s; omitting thumbnail",
                        servant.id, exc_info=True)
        return None

    async def replace_drop(self, channel, user_id: int, source: str) -> bool:
        """True handles the drop slot, including suppressed spawns during Halloween."""
        if not self.bot.config.halloween_active():
            return False
        # The seasonal window replaces normal drops for everyone, while the
        # whitelist still limits who can trigger and claim Halloween events.
        if not self.allowed(user_id):
            return True
        grails = int(source.startswith("grail"))
        kind = "candy_spill" if source.endswith("box") else "trick_or_treat"
        if source == "grail_box":
            slots = random.randint(contract_game.GRAIL_BOX_USES_MIN, contract_game.GRAIL_BOX_USES_MAX)
        elif source == "ember_box":
            slots = random.randint(contract_game.EMBER_BOX_USES_MIN, contract_game.EMBER_BOX_USES_MAX)
        else:
            slots = 1
        # A busy channel or denied spawn must never fall back to a normal event.
        await self.spawn_event(channel, kind, slots=slots, grails=grails)
        return True

    async def spawn_event(self, channel, kind: str, *, slots: int = 5, grails: int = 0) -> bool:
        if not self.bot.config.halloween_active() or channel.id in self._channels:
            return False
        parent_id = channel.parent_id if isinstance(channel, discord.Thread) else None
        if not await self.bot.guild_config.is_event_channel_allowed(channel.guild.id, channel.id, parent_id):
            return False
        # Recheck after the asynchronous channel lookup, before reserving the channel.
        if channel.id in self._channels or not self.bot.config.halloween_active():
            return False
        self._channels.add(channel.id)
        view = None
        try:
            expires = time.time() + H.EVENT_SECONDS
            end = self.bot.config.halloween_end_at
            if end is not None:
                expires = min(expires, end.timestamp())
            event = await self.service.create_event(
                channel.guild.id, channel.id, kind, slots=slots, grails=grails, expires_at=expires,
            )
            face = await self._face(self.bot.servants.get(H.HOST_ID))
            view = CandyEventView(self, event, face)
            self._views.add(view)
            view.message = await channel.send(
                embed=view.render(), view=view, allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except Exception:
            self._channels.discard(channel.id)
            if view is not None:
                view.stop()
                self._views.discard(view)
            raise

    @app_commands.command(name="halloweenevent", description="(Mods) Spawn a Halloween event in an event channel.")
    @app_commands.guild_only()
    @app_commands.choices(event=_EVENT_CHOICES)
    @app_commands.describe(event="Which Halloween event to spawn", channel="An allowed event channel (defaults to here)")
    async def halloweenevent(
        self, interaction: discord.Interaction, event: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not self.allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message("You need moderator permissions to spawn an event.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        spawned = await self.spawn_event(target, event.value)
        text = (f"Spawned {event.name} in {target.mention}." if spawned else
                "No event spawned. Check the event dates, /summonconfig eventchannel, and whether an event is already running.")
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="candy", description="Feed Halloween candy to one of your servants for XP.")
    @app_commands.guild_only()
    @app_commands.describe(servant="Your servant to feed (defaults to your active one)", quantity="Candy to feed (default 1)")
    async def candy(
        self, interaction: discord.Interaction, servant: int | None = None,
        quantity: app_commands.Range[int, 1, 1000] = 1,
    ) -> None:
        if not self.allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id
        if servant is None:
            active = await self.bot.contracts.active(gid, uid)
            if active is None:
                return await interaction.followup.send("You have no active servant. Use /summon first.", ephemeral=True)
            servant = active["servant_id"]
        s = self.bot.servants.get(servant)
        status, used, old, new, cap = await self.bot.contracts.feed_xp(gid, uid, uid, servant, "candy", quantity)
        if status != "ok":
            text = {
                "not_owned": "You don't own that servant. Check /servants for your roster.",
                "no_items": "You have no candy. Collect some from Trick or Treat and Candy Spill events.",
                "at_cap": f"That servant is at its level cap ({cap}). Use /grail first. Your candy was kept.",
            }[status]
            return await interaction.followup.send(text, ephemeral=True)
        name = s.name if s else "Your servant"
        description = f"Fed **{used} candy** to **{name}**.\nLv {old} -> **{new}**"
        if new >= cap:
            description += " (cap reached)"
        if used < quantity:
            description += "\nUsed only the candy available and needed to reach the cap."
        lines = s.growth_lines if s else ()
        description += f'\n\n*"{random.choice(lines)}"*' if lines else f"\n\n{name} enjoys the Halloween treats."
        embed = discord.Embed(title="A Halloween Treat", description=description, color=discord.Color.orange())
        face = await self._face(s)
        if face:
            embed.set_thumbnail(url=face)
        remaining = await self.service.balance(gid, uid)
        embed.set_footer(text=f"{remaining:,} candy left | {contract_game.CANDY_XP:,} XP per candy")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @candy.autocomplete("servant")
    async def candy_servants(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        if not interaction.guild_id or not self.allowed(interaction.user.id):
            return []
        roster = await self.bot.contracts.owned(interaction.guild_id, interaction.user.id)
        choices = []
        for row in roster:
            s = self.bot.servants.get(row["servant_id"])
            if s and (not current or current.casefold() in s.name.casefold()):
                choices.append(app_commands.Choice(name=f"{s.name[:70]} (Lv {row['level']})", value=s.id))
        return choices[:25]


class CandyEventView(discord.ui.View):
    def __init__(self, cog: HalloweenCog, event: dict, face: str | None) -> None:
        # Discord's built-in timeout resets on clicks, including rejected ones. This event
        # has an absolute deadline, so repeated clicks must not keep it occupying a channel.
        super().__init__(timeout=None)
        self.cog, self.event, self.face = cog, event, face
        self.message: discord.Message | None = None
        self.claimed_slots: tuple[int, ...] = ()
        self.claim_log: list[str] = []
        self.outcome_flavor: str | None = None
        self.complete = False
        self.expired = False
        self._lock = asyncio.Lock()
        self._expiry_task = asyncio.create_task(self._expire_at_deadline())
        if event["kind"] == "trick_or_treat":
            self._button("Treat", "treat", 0, discord.ButtonStyle.success)
            self._button("Trick", "trick", 0, discord.ButtonStyle.primary)
        else:
            for slot in range(event["slots"]):
                self._button(f"Candy {slot + 1}", "collect", slot, discord.ButtonStyle.primary)

    async def _expire_at_deadline(self) -> None:
        await asyncio.sleep(max(0, self.event["expires_at"] - time.time()))
        await self.on_timeout()

    def stop(self) -> None:
        if self._expiry_task is not asyncio.current_task():
            self._expiry_task.cancel()
        super().stop()

    def _button(self, label: str, action: str, slot: int, style) -> None:
        button = discord.ui.Button(label=label, style=style, row=slot // 5)

        async def callback(interaction):
            await self.claim(interaction, action, slot)

        button.callback = callback
        self.add_item(button)

    def render(self) -> discord.Embed:
        spill = self.event["kind"] == "candy_spill"
        description = (
            '**Liz:** "Master! The candy was supposed to fall AFTER my grand entrance! Catch it!"\n\n'
            f"{H.SPILL_CANDY} candy per player. Collect all {self.event['slots']} to give every participant "
            f"**{H.SPILL_BONUS} bonus candy**."
            if spill else
            '**Liz:** "Trick or treat, my little fans? Choose wisely!"'
        )
        if self.outcome_flavor:
            description = self.outcome_flavor
        if self.event["grails"]:
            description += "\nEach claim also includes **1 Holy Grail**."
        if self.complete:
            description += "\n\nAll collected!" if spill else "\n\nEvent claimed!"
            if spill:
                description += f" Every participant received **{H.SPILL_BONUS} extra candy**."
        elif self.expired:
            description += "\n\nEvent ended."
        else:
            description += f"\nEnds <t:{int(self.event['expires_at'])}:R>."
        embed = discord.Embed(title="Candy Spill!" if spill else "Trick or Treat!",
                              description=description, color=discord.Color.orange())
        if self.face:
            embed.set_thumbnail(url=self.face)
        if self.claim_log:
            embed.add_field(name="Treats collected" if spill else "Result",
                            value="\n".join(self.claim_log)[-1024:], inline=False)
        footer = f"{len(self.claimed_slots)}/{self.event['slots']} claimed"
        if spill:
            footer += " | One claim per player"
        embed.set_footer(text=footer)
        return embed

    def _finish(self) -> None:
        for button in self.children:
            button.disabled = True
        self.stop()
        self.cog._views.discard(self)
        self.cog._channels.discard(self.event["channel_id"])

    async def _refresh(self) -> None:
        if self.message:
            try:
                await self.message.edit(embed=self.render(), view=self,
                                        delete_after=15 if self.complete or self.expired else None)
            except discord.HTTPException:
                log.warning("Couldn't update Halloween event %s; earned rewards were saved", self.event["id"])

    async def on_timeout(self) -> None:
        async with self._lock:
            self.expired = True
            self._finish()
            await self._refresh()

    async def claim(self, interaction: discord.Interaction, action: str, slot: int) -> None:
        if not self.cog.allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self._lock:
            if self.expired or not self.cog.bot.config.halloween_active():
                return await interaction.followup.send("This Halloween event has ended.", ephemeral=True)
            result = await self.cog.service.claim(
                self.event["id"], interaction.guild_id, interaction.channel_id, interaction.user.id,
                action=action, slot=slot,
            )
            if result.status != "ok":
                text = {"expired": "This Halloween event has ended.",
                        "duplicate": "You already collected candy from this event.",
                        "taken": "Someone already collected that treat.",
                        "invalid": "That treat is unavailable."}[result.status]
                return await interaction.followup.send(text, ephemeral=True)
            self.claimed_slots = result.claimed_slots
            self.complete = result.complete
            if self.event["kind"] == "candy_spill":
                self.children[slot].disabled = True
                self.children[slot].label = "Collected"
            name = discord.utils.escape_markdown(interaction.user.display_name)
            reward = f"{result.candy} candy" + (" + 1 Holy Grail" if result.grails else "")
            loss = f" Lost **{result.qp_lost:,} QP**." if result.qp_lost else ""
            self.claim_log.append(f"**{name}** received {reward}.{loss}")
            if self.complete:
                self._finish()
            face = await self.cog._face(self.cog.bot.servants.get(result.servant_id))
            if self.event["kind"] == "trick_or_treat":
                self.outcome_flavor = result.flavor
                self.face = face
            await self._refresh()
            text = f"{result.flavor}\nYou received **{reward}**."
            if result.qp_penalty:
                if result.qp_lost:
                    text += f"\nYou lost **{result.qp_lost:,} QP**."
                    if result.qp_lost < result.qp_penalty:
                        text += " The penalty was limited to your available QP."
                else:
                    text += "\nYou had no QP to take, so you lost nothing."
                text += f"\nQP remaining: **{result.qp_balance:,}**."
            if result.bonus:
                text += f" Everyone also received **{result.bonus} bonus candy**."
            text += f"\nYou now have **{result.balance} candy**. Use /candy to feed your servant."
            title = {"trick": "Trick!", "treat": "Treat!", "collect": "Candy Collected!"}[action]
            embed = discord.Embed(title=title, description=text, color=discord.Color.orange())
            if face:
                embed.set_thumbnail(url=face)
            await interaction.followup.send(embed=embed, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


async def setup(bot) -> None:
    await bot.add_cog(HalloweenCog(bot))
