from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp
import discord

from branding import qp, qp_emote
from data import host, matching
from data.servants import Servant
from permissions import is_mod

log = logging.getLogger(__name__)

ROUND_TIMEOUT = 900  # seconds (15 minutes); also the round lifetime
RECENT_PICKS = 20  # per channel: don't re-pick a servant within this many rounds

TITLES = {
    "guess_servant": "Guess the Servant",
    "guess_shadow": "Guess the Shadow",
    "guess_audio": "Guess the Voice",
}

WIN_REACTION = "\N{WHITE HEAVY CHECK MARK}"
WRONG_REACTION = "\N{CROSS MARK}"
FORFEIT_EMOTE = "\N{WAVING WHITE FLAG}\N{VARIATION SELECTOR-16}"  # vote-to-give-up react
FORFEIT_VOTES = 3  # distinct humans whose reaction ends the round
HINT_REWARD = (1.0, 0.6, 0.3)  # win multiplier by hints used (0, 1, 2): hints cost QP

_NO_PINGS = discord.AllowedMentions.none()
_PING_USER = discord.AllowedMentions(everyone=False, roles=False, users=True)


@dataclass
class Media:
    """A prompt or reveal asset: either a remote URL (set_image directly, e.g. a
    precomputed S3 silhouette) or raw bytes to attach (a runtime crop / mp3)."""

    is_image: bool
    url: str | None = None
    data: bytes | None = None
    filename: str = "prompt.png"


# (allow) -> (servant, ascension) or None
Picker = Callable[[Callable[[int, str], bool]], "tuple[Servant, str] | None"]
# (session, servant, ascension) -> Media
MediaBuilder = Callable[[aiohttp.ClientSession, Servant, str], Awaitable[Media]]


def _attach(embed: discord.Embed, media: Media | None) -> discord.File | None:
    """Apply media to an embed; return a File to send if the media is byte data."""
    if media is None:
        return None
    if media.url is not None:
        if media.is_image:
            embed.set_image(url=media.url)
        return None
    file = discord.File(io.BytesIO(media.data or b""), filename=media.filename)
    if media.is_image:
        embed.set_image(url=f"attachment://{media.filename}")
    return file


def _host_author(embed: discord.Embed, host_id: str) -> None:
    embed.set_author(name=host.name(host_id))
    portrait = host.portrait(host_id)
    if portrait:
        embed.set_thumbnail(url=portrait)


class PlayAgainView(discord.ui.View):
    """A 'Play Again' button on a reveal that re-runs the same game in-channel."""

    def __init__(self, replay: "Callable[[discord.Interaction], Awaitable[None]]") -> None:
        super().__init__(timeout=600)
        self._replay = replay
        self.message: discord.Message | None = None

    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.secondary)
    async def play_again(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # One-shot: grey this button out so it can't be re-clicked or raced (the new
        # round carries its own Play Again). The launch reservation is the real race
        # guard; this is UX so a stale button doesn't keep starting games.
        # Only retire the button if a round actually started; on a failed replay
        # (e.g. couldn't load) leave it clickable so they can try again.
        started = await self._replay(interaction)
        if started:
            button.disabled = True
            self.stop()
            if self.message is not None:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ChatRound:
    """One in-flight round. Players answer by typing the servant's name in the
    channel; the on_message listener in cogs/chat_guess.py routes messages here.

    We *react* rather than reply (check on the winner, cross on a wrong-but-real
    guess) so the channel never fills with bot messages -- and Discord cannot send
    an ephemeral reply to a plain message anyway. The round ends on the first
    correct answer or the timeout; there is no shared wrong-guess budget."""

    def __init__(
        self,
        *,
        bot,
        game_id: int,
        host_id: str,
        servant: Servant,
        ascension: str,
        points: int,
        build_reveal: MediaBuilder,
        prompt: Media | None = None,
        replay: "Callable[[discord.Interaction], Awaitable[None]] | None" = None,
    ) -> None:
        self.bot = bot
        self.game_id = game_id
        self.host_id = host_id
        self.servant = servant
        self.ascension = ascension
        self.points = points
        self._build_reveal = build_reveal
        # The prompt asset (cropped art / silhouette / mp3) is kept so we can leave it
        # visible on the prompt after the round ends, for retrospect.
        self.prompt = prompt
        self.replay = replay
        self.channel_id: int | None = None
        self.message: discord.Message | None = None
        self.expires_at: dt.datetime | None = None
        self.claimed = False
        self.hints_given = 0
        self.vote_message: discord.Message | None = None
        self.vote_reactors: set[int] = set()
        self._timeout_task: asyncio.Task | None = None

    def start(self, channel_id: int, message: discord.Message) -> None:
        self.channel_id = channel_id
        self.message = message
        self.bot.active_rounds[channel_id] = self
        self._timeout_task = asyncio.create_task(self._run_timeout())

    def _unregister(self) -> None:
        if self.channel_id is not None:
            self.bot.active_rounds.pop(self.channel_id, None)
        if self.vote_message is not None:
            self.bot.forfeit_votes.pop(self.vote_message.id, None)

    async def give_hint(self, channel: discord.abc.Messageable) -> None:
        """On '@bot hint': reveal rarity, then class, on successive asks."""
        if self.claimed:
            return
        hints: list[tuple[str, str]] = []
        if self.servant.rarity:
            hints.append(("Rarity", f"{self.servant.rarity}-star"))
        if self.servant.class_name:
            hints.append(("Class", self.servant.class_name.title()))
        if self.hints_given >= len(hints):
            recap = "\n".join(
                f"Hint {i + 1}: {lbl} is **{val}**." for i, (lbl, val) in enumerate(hints)
            )
            await channel.send(f"That's all the hints. Type your guess.\n\n{recap}")
            return
        label, value = hints[self.hints_given]
        self.hints_given += 1
        await channel.send(
            f"Hint {self.hints_given}: {label} is **{value}**.  "
            f"Reward drops to **{qp(self._effective_points())}**."
        )

    def _effective_points(self) -> int:
        """The win, reduced by how many hints were taken (HINT_REWARD); floored at 1."""
        mult = HINT_REWARD[min(self.hints_given, len(HINT_REWARD) - 1)]
        return max(1, round(self.points * mult))

    def _votes_needed(self) -> int:
        return max(1, FORFEIT_VOTES - len(self.vote_reactors))

    def _vote_text(self) -> str:
        n = self._votes_needed()
        return (
            f"Give up? React with {FORFEIT_EMOTE} to vote. "
            f"**{n} more** vote{'' if n == 1 else 's'} needed to reveal the answer."
        )

    async def start_forfeit_vote(self, channel: discord.abc.Messageable) -> None:
        """On '@bot give up': post a give-up vote and seed the flag so players can
        one-tap it. The bot's own seed isn't counted (we skip it in the listener);
        the '<n> more needed' text -- not the raw reaction count -- is the truth."""
        if self.claimed:
            return
        if self.vote_message is not None:
            # Already running -- re-point to it so a repeat ask isn't silent.
            try:
                await channel.send(
                    f"A give-up vote is already up (**{self._votes_needed()} more** needed). "
                    f"React with {FORFEIT_EMOTE} here: {self.vote_message.jump_url}"
                )
            except discord.HTTPException:
                pass
            return
        msg = await channel.send(self._vote_text())
        self.vote_message = msg
        self.bot.forfeit_votes[msg.id] = self
        await self._react(msg, FORFEIT_EMOTE)

    async def register_vote(self, user_id: int) -> bool:
        """Count a give-up reaction; returns True once the threshold is met."""
        if self.claimed or self.vote_message is None:
            return False
        self.vote_reactors.add(user_id)
        if len(self.vote_reactors) >= FORFEIT_VOTES:
            return True
        await self._refresh_vote()
        return False

    async def withdraw_vote(self, user_id: int) -> None:
        if self.claimed or self.vote_message is None:
            return
        self.vote_reactors.discard(user_id)
        await self._refresh_vote()

    async def _refresh_vote(self) -> None:
        if self.vote_message is None:
            return
        try:
            await self.vote_message.edit(content=self._vote_text())
        except discord.HTTPException:
            pass

    async def handle_message(self, message: discord.Message) -> None:
        if self.claimed:
            return
        aliases = self.bot.aliases.for_servant(self.servant.id)
        if self.servant.aliases:
            # NPC bosses carry their accepted answers statically (npc_servants.json);
            # fold them in alongside any DB-curated aliases, normalized to match.
            aliases = aliases | frozenset(
                matching.normalize(a) for a in self.servant.aliases
            )
        if matching.is_correct_guess(
            message.content,
            self.servant.name,
            aliases=aliases,
            all_names=self.bot.servants.spaced_names(),
        ):
            await self._win(message)
        elif self.bot.servants.resembles_servant(
            message.content, extra=self.bot.aliases.all_terms()
        ):
            await self._react(message, WRONG_REACTION)

    async def _win(self, message: discord.Message) -> None:
        self.claimed = True
        self._cancel_timeout()
        self._unregister()
        award = self._effective_points()
        total = await self.bot.scoring.award(message.guild.id, message.author.id, award)
        await self.bot.games.resolve(self.game_id, "win", message.author.id, award)
        await self._react(message, WIN_REACTION)
        await self._react(message, discord.PartialEmoji.from_str(qp_emote()))
        # Post the FULL reveal at the bottom, where the action is -- nobody
        # backscrolls to the prompt -- tagging the winner with their reward.
        praise = host.line(self.host_id, "correct", player=message.author.display_name)
        embed, file = await self._build_reveal_embed(f"*{praise}*")
        embed.color = discord.Color.green()
        reward = f"+{qp(award)} (QP {qp(total)})"
        if self.hints_given:
            reward += f"  ·  {self.hints_given} hint{'s' if self.hints_given > 1 else ''} used"
        embed.add_field(name="Reward", value=reward, inline=False)
        await self._post_reveal(message.channel, embed, file, ping=message.author.mention)
        # Slim the now-buried prompt so it isn't left showing "type the name".
        await self._slim_prompt(f"Solved by {message.author.display_name}.")

    async def _post_reveal(
        self,
        channel: discord.abc.Messageable,
        embed: discord.Embed,
        file: discord.File | None,
        *,
        ping: str | None = None,
    ) -> None:
        """Send a reveal at the bottom of the channel, with a Play Again button."""
        view = PlayAgainView(self.replay) if self.replay is not None else None
        kwargs: dict = {}
        if file is not None:
            kwargs["file"] = file
        if view is not None:
            kwargs["view"] = view
        if ping is not None:
            kwargs["content"] = ping
            kwargs["allowed_mentions"] = _PING_USER
        try:
            sent = await channel.send(embed=embed, **kwargs)
            if view is not None:
                view.message = sent
        except discord.HTTPException:
            log.exception("failed to post reveal for game %s", self.game_id)

    async def _slim_prompt(self, note: str) -> None:
        """Edit the now-buried prompt to a terminal state but KEEP its cropped image /
        audio clip, so players can scroll back and see what the round asked. The crop
        or mp3 is re-uploaded (attachment://) so it stays inside the embed; a remote-URL
        silhouette is just re-pointed and needs no attachment."""
        embed = discord.Embed(title=f"It was {self.servant.name}!", description=note)
        file = _attach(embed, self.prompt)
        kwargs: dict = {"embed": embed}
        if file is not None:
            kwargs["attachments"] = [file]
        await self._edit_prompt(**kwargs)

    async def _edit_prompt(self, **kwargs) -> None:
        """Edit the prompt via the CHANNEL (bot auth), not the interaction followup
        webhook. The interaction token expires at ~15 min -- exactly ROUND_TIMEOUT --
        so a webhook edit then 401s with `50027 Invalid Webhook Token`."""
        if self.message is None or self.channel_id is None:
            return
        channel = self.bot.get_channel(self.channel_id)
        get_partial = getattr(channel, "get_partial_message", None)
        if get_partial is None:
            return
        try:
            await get_partial(self.message.id).edit(**kwargs)
        except discord.HTTPException:
            log.exception("failed to edit prompt for game %s", self.game_id)

    async def forfeit(self, channel: discord.abc.Messageable) -> None:
        """End the round early (mod/owner) and reveal the answer at the bottom."""
        if self.claimed:
            return
        self.claimed = True
        self._cancel_timeout()
        self._unregister()
        await self.bot.games.resolve(self.game_id, "forfeited", None, 0)
        headline = f'*{host.line(self.host_id, "reveal", answer=self.servant.name)}*'
        embed, file = await self._build_reveal_embed(headline)
        await self._post_reveal(channel, embed, file)
        await self._slim_prompt("Round ended.")

    async def _run_timeout(self) -> None:
        try:
            await asyncio.sleep(ROUND_TIMEOUT)
        except asyncio.CancelledError:
            return
        if self.claimed:
            return
        self.claimed = True
        self._unregister()
        await self.bot.games.resolve(self.game_id, "timeout", None, 0)
        # Post the reveal at the BOTTOM so a timeout doesn't die silently up in the
        # buried prompt; then slim the prompt itself.
        headline = f'*{host.line(self.host_id, "reveal", answer=self.servant.name)}*'
        embed, file = await self._build_reveal_embed(headline)
        channel = self.bot.get_channel(self.channel_id) if self.channel_id else None
        if channel is not None:
            await self._post_reveal(channel, embed, file)
        await self._slim_prompt("Time's up.")

    def _cancel_timeout(self) -> None:
        if self._timeout_task is not None:
            self._timeout_task.cancel()

    @staticmethod
    async def _react(message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass

    async def _build_reveal_embed(
        self, headline: str
    ) -> tuple[discord.Embed, discord.File | None]:
        try:
            media: Media | None = await self._build_reveal(
                self.bot.http_session, self.servant, self.ascension
            )
        except Exception:
            log.exception("reveal build failed for servant %s", self.servant.id)
            media = None
        s = self.servant
        embed = discord.Embed(title=f"It was {s.name}!", description=headline)
        _host_author(embed, self.host_id)
        if s.class_name:
            embed.add_field(name="Class", value=s.class_name.title())
        if s.rarity:
            embed.add_field(name="Rarity", value=f"{s.rarity}-star")
        if s.cv:
            embed.add_field(name="CV", value=s.cv)
        file = _attach(embed, media)
        return embed, file


async def launch_round(
    cog,
    interaction: discord.Interaction,
    *,
    game_type: str,
    host_id: str,
    points: int,
    picker: Picker,
    build_prompt: MediaBuilder,
    build_reveal: MediaBuilder,
    difficulty: str | None = None,
) -> bool:
    bot = cog.bot
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return False

    cfg = await bot.guild_config.get(interaction.guild_id)
    if not bot.guild_config.game_enabled(cfg, game_type):
        await interaction.response.send_message("That game is turned off here.", ephemeral=True)
        return False
    channel_is_game = await bot.guild_config.is_channel_allowed(
        interaction.guild_id, interaction.channel_id
    )
    if not channel_is_game:
        # Regular players are confined to the configured game channel(s); mods (and
        # the owner) can still drop a round into any channel for the occasional run.
        if not (is_mod(interaction.user) or await bot.is_owner(interaction.user)):
            channels = ", ".join(f"<#{c}>" for c in cfg["allowed_channel_ids"][:5])
            await interaction.response.send_message(
                f"Games are played in {channels}. A mod can start a round in any channel.",
                ephemeral=True,
            )
            return False
    existing = bot.active_rounds.get(interaction.channel_id)
    if (existing is not None and not existing.claimed) or interaction.channel_id in bot.launching:
        when = ""
        if existing is not None and existing.expires_at is not None:
            when = f" (ends <t:{int(existing.expires_at.timestamp())}:R>)"
        await interaction.response.send_message(
            f"A round is already running in this channel{when}. "
            "Just type the servant's name to play, or tag "
            f"{bot.user.mention} with **forfeit** to start a give-up vote.",
            ephemeral=True,
        )
        return False

    # Reserve the channel synchronously (no await between the check above and this)
    # so two near-simultaneous starts -- e.g. racing Play Again clicks -- can't both
    # pass and spawn two rounds. Released in finally once the round is registered.
    bot.launching.add(interaction.channel_id)
    try:
        await interaction.response.defer()
        allow = await bot.restrictions.build_allow()
        # Skip servants this channel saw in its last RECENT_PICKS rounds, so the
        # same one (incl. via Play Again) doesn't appear back to back.
        recent = bot.recent_picks.setdefault(
            interaction.channel_id, deque(maxlen=RECENT_PICKS)
        )

        def fresh(sid: int, asc: str) -> bool:
            return sid not in recent and allow(sid, asc)

        # Retry across a few servants: a voice pick may land on a servant with no
        # clip, or an art fetch may blip -- don't fail the whole round for that.
        servant = ascension = prompt = None
        for attempt in range(4):
            picked = picker(fresh)
            if picked is None:
                break
            cand_servant, cand_ascension = picked
            try:
                prompt = await build_prompt(bot.http_session, cand_servant, cand_ascension)
                servant, ascension = cand_servant, cand_ascension
                break
            except Exception:
                log.warning(
                    "prompt build failed for servant %s (attempt %d/4)",
                    cand_servant.id,
                    attempt + 1,
                )
        if prompt is None:
            await interaction.followup.send("Couldn't start a round right now. Try again.")
            return False
        recent.append(servant.id)

        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ROUND_TIMEOUT)
        game_id = await bot.games.open_round(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            game_type=game_type,
            servant_id=servant.id,
            ascension=ascension,
            answer_name=servant.name,
            points=points,
            started_by=interaction.user.id,
            expires_at=expires_at,
        )

        async def _replay(again: discord.Interaction) -> bool:
            # Re-run the same game (same difficulty/host) from the Play Again button.
            return await launch_round(
                cog,
                again,
                game_type=game_type,
                host_id=host_id,
                points=points,
                picker=picker,
                build_prompt=build_prompt,
                build_reveal=build_reveal,
                difficulty=difficulty,
            )

        round_ = ChatRound(
            bot=bot,
            game_id=game_id,
            host_id=host_id,
            servant=servant,
            ascension=ascension,
            points=points,
            build_reveal=build_reveal,
            prompt=prompt,
            # No Play Again in a non-game channel (a mod-triggered one-off): regular
            # players can't start rounds there, so the button would only reject them.
            replay=_replay if channel_is_game else None,
        )
        round_.expires_at = expires_at
        intro = host.line(host_id, "start", player=interaction.user.display_name)
        title = TITLES.get(game_type, "Guess the Servant")
        if difficulty:
            title = f"{title} - {difficulty.title()}"
        if game_type == "guess_audio":
            # The host is itself a Servant, so be explicit that the clip is the
            # Servant to identify -- otherwise players read the host's portrait as
            # the voice in the clip.
            how = (
                "**Listen to the clip and type the servant's name in chat.** "
                "The voice you hear is the mystery servant."
            )
        else:
            how = "**Type the servant's name in chat** to answer."
        embed = discord.Embed(
            title=title,
            description=(
                f"*{intro}*\n\n{how} "
                f"Reward: **{points:,}** {qp_emote()}.\n"
                f"Game ends <t:{int(expires_at.timestamp())}:R>."
            ),
        )
        _host_author(embed, host_id)
        prompt_file = _attach(embed, prompt)
        kwargs = {"file": prompt_file} if prompt_file else {}
        message = await interaction.followup.send(embed=embed, **kwargs)
        round_.start(interaction.channel_id, message)
        await bot.games.attach_message(game_id, message.id)

        await _post_audit_log(
            bot, cfg, game_type, servant, ascension, interaction, message, prompt
        )
        return True
    finally:
        bot.launching.discard(interaction.channel_id)


async def tidy_old_prompt(bot, channel_id: int, message_id, answer_name: str) -> None:
    """Edit a stranded prompt to a terminal state so it isn't left telling players to
    guess a round that's already over."""
    if not message_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return
    try:
        msg = await channel.fetch_message(message_id)
        embed = discord.Embed(title=f"It was {answer_name}!", description="Round ended.")
        # Keep the prompt's image / clip for retrospect. An uploaded crop or mp3 stays
        # as long as we don't clear attachments; a remote-URL image (no attachment of
        # its own) has to be re-set on the new embed.
        old = msg.embeds[0] if msg.embeds else None
        if old is not None and old.image.url and not msg.attachments:
            embed.set_image(url=old.image.url)
        await msg.edit(embed=embed)
    except discord.HTTPException:
        pass


async def _post_audit_log(
    bot, cfg, game_type, servant, ascension, interaction, message, prompt: Media
) -> None:
    channel_id = cfg["log_channel_id"]
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return
    embed = discord.Embed(title=f"Game started: {TITLES.get(game_type, game_type)}")
    embed.add_field(name="Answer", value=servant.name)
    embed.add_field(name="Servant ID", value=str(servant.id))
    embed.add_field(name="Ascension", value=str(ascension))
    if servant.class_name:
        embed.add_field(name="Class", value=servant.class_name.title())
    if servant.rarity:
        embed.add_field(name="Rarity", value=f"{servant.rarity}-star")
    embed.add_field(name="Started by", value=interaction.user.mention)
    embed.add_field(
        name="Where", value=f"<#{interaction.channel_id}> ([jump]({message.jump_url}))"
    )
    log_file = _attach(embed, prompt)
    kwargs = {"file": log_file} if log_file else {}
    try:
        await channel.send(embed=embed, allowed_mentions=_NO_PINGS, **kwargs)
    except discord.HTTPException:
        log.exception("failed to post to audit-log channel %s", channel_id)
