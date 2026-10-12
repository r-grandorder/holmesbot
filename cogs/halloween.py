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
_DENY = "You don't have access to the Halloween event."
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
            # Sending can wait on Discord's rate limit. Start the timer only once
            # the message exists, and keep early clicks behind the activation lock.
            async with view._lock:
                view.message = await channel.send(
                    embed=view.render(), view=view, allowed_mentions=discord.AllowedMentions.none(),
                )
                deadline = time.time() + H.EVENT_SECONDS
                if end is not None:
                    deadline = min(deadline, end.timestamp())
                await self.service.set_deadline(event["id"], deadline)
                event["expires_at"] = deadline
                active = deadline > time.time()
                if active:
                    view.start_timer()
            if not active:
                await view.on_timeout()
                return False
            if int(deadline) != int(expires):
                await view._update_message()
            log.info("Started Halloween event %s in channel %s; claim window %.1fs",
                     event["id"], channel.id, max(0, deadline - time.time()))
            return True
        except Exception:
            self._channels.discard(channel.id)
            if view is not None:
                view.stop()
                self._views.discard(view)
                if view.message is not None:
                    await view._delete_message(delay=0)
            raise

    @app_commands.command(name="halloweenevent", description="(Mods) Start a Halloween event.")
    @app_commands.guild_only()
    @app_commands.choices(event=_EVENT_CHOICES)
    @app_commands.describe(event="Event to start", channel="Event channel (defaults to here)")
    async def halloweenevent(
        self, interaction: discord.Interaction, event: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not self.allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message("Only moderators can start events.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        spawned = await self.spawn_event(target, event.value)
        text = (f"Started {event.name} in {target.mention}." if spawned else
                "Couldn't start the event. Check its dates, /summonconfig eventchannel, and any active event here.")
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="candy", description="Feed Halloween candy to your servant.")
    @app_commands.guild_only()
    @app_commands.describe(servant="Servant to feed (defaults to your active one)", quantity="Candy to use (default 1)")
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
                return await interaction.followup.send("Choose a servant to feed, or summon one with /summon.", ephemeral=True)
            servant = active["servant_id"]
        s = self.bot.servants.get(servant)
        status, used, old, new, cap = await self.bot.contracts.feed_xp(gid, uid, uid, servant, "candy", quantity)
        if status != "ok":
            text = {
                "not_owned": "You don't own that servant. Check /servants.",
                "no_items": "You have no candy.",
                "at_cap": f"Already at the Lv {cap} cap. Use /grail to raise it.",
            }[status]
            return await interaction.followup.send(text, ephemeral=True)
        name = s.name if s else "Your servant"
        description = f"Fed **{used} candy** to **{name}**.\nLv {old} -> **{new}**"
        if new >= cap:
            description += " (cap reached)"
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
        self._expiry_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        if event["kind"] == "trick_or_treat":
            self._button("Treat", "treat", 0, discord.ButtonStyle.success)
            self._button("Trick", "trick", 0, discord.ButtonStyle.primary)
        else:
            for slot in range(event["slots"]):
                self._button(f"Candy {slot + 1}", "collect", slot, discord.ButtonStyle.primary)

    def start_timer(self) -> None:
        if self._expiry_task is None and not self.is_finished():
            self._expiry_task = asyncio.create_task(self._expire_at_deadline())

    async def _expire_at_deadline(self) -> None:
        await asyncio.sleep(max(0, self.event["expires_at"] - time.time()))
        await self.on_timeout()

    def stop(self) -> None:
        if self._expiry_task is not None and self._expiry_task is not asyncio.current_task():
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
            '**Liz:** "Master! My candy! Catch it!"'
            if spill else
            '**Liz:** "Trick or treat, my little fans? Choose wisely!"'
        )
        if self.outcome_flavor:
            description = self.outcome_flavor
        if spill and not self.complete and not self.expired:
            description += f"\n\nCollect all {self.event['slots']} to get {H.SPILL_BONUS} bonus candy!"
        if self.event["grails"]:
            description += "\n**1 Holy Grail** per claim."
        if self.complete:
            if spill:
                description += f"\n\nAll collected! **{H.SPILL_BONUS} bonus candy** for each player."
        elif self.expired:
            description += "\n\nEvent ended."
        else:
            description += f"\nEnds <t:{int(self.event['expires_at'])}:R>."
        embed = discord.Embed(title="Candy Spill!" if spill else "Trick or Treat!",
                              description=description, color=discord.Color.orange())
        if self.face:
            embed.set_thumbnail(url=self.face)
        if self.claim_log:
            embed.add_field(name="Collected" if spill else "Result",
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

    async def _update_message(self) -> None:
        # View._refresh(components) belongs to discord.py's gateway handling.
        if self.message:
            # Cleanup must run even if editing the ended message fails or stalls.
            if (self.complete or self.expired) and self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(
                    self._delete_message(delay=0 if self.expired else 15),
                )
            try:
                await self.message.edit(embed=self.render(), view=self)
            except discord.NotFound:
                pass  # The cleanup task may already have removed an expired message.
            except discord.HTTPException:
                log.warning("Couldn't update Halloween event %s; earned rewards were saved", self.event["id"])

    async def _delete_message(self, *, delay: float) -> None:
        await asyncio.sleep(delay)
        for attempt in range(3):
            try:
                await self.message.delete()
                return
            except discord.NotFound:
                return
            except discord.HTTPException:
                if attempt == 2:
                    log.warning("Couldn't delete Halloween event message %s", self.message.id, exc_info=True)
                    return
                await asyncio.sleep(2 * (attempt + 1))

    async def on_timeout(self) -> None:
        async with self._lock:
            self.expired = True
            self._finish()
            await self._update_message()

    async def claim(self, interaction: discord.Interaction, action: str, slot: int) -> None:
        if not self.cog.allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self._lock:
            if self.expired or not self.cog.bot.config.halloween_active():
                return await interaction.followup.send("Event ended.", ephemeral=True)
            result = await self.cog.service.claim(
                self.event["id"], interaction.guild_id, interaction.channel_id, interaction.user.id,
                action=action, slot=slot,
            )
            if result.status != "ok":
                text = {"expired": "Event ended.",
                        "duplicate": "You already claimed this event.",
                        "taken": "Already claimed.",
                        "invalid": "That candy is unavailable."}[result.status]
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
            await self._update_message()
            text = f"{result.flavor}\n\nReceived **{reward}**."
            if result.qp_penalty:
                if result.qp_lost:
                    text += f"\nLost **{result.qp_lost:,} QP**."
                else:
                    text += "\nNo QP to take."
            if result.bonus:
                text += f"\n**{result.bonus} bonus candy** for each player!"
            title = {"trick": "Trick!", "treat": "Treat!", "collect": "Candy Collected!"}[action]
            embed = discord.Embed(title=title, description=text, color=discord.Color.orange())
            footer = f"{result.balance:,} candy left"
            if result.qp_balance is not None:
                footer += f" | {result.qp_balance:,} QP left"
            embed.set_footer(text=footer)
            if face:
                embed.set_thumbnail(url=face)
            await interaction.followup.send(embed=embed, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


async def setup(bot) -> None:
    await bot.add_cog(HalloweenCog(bot))
