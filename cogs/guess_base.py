from __future__ import annotations

import asyncio
import copy
import datetime as dt
import io
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp
import discord
from discord import app_commands

from branding import qp, qp_emote
from data import host, matching
from data.servants import Servant, class_display
from permissions import is_mod

log = logging.getLogger(__name__)

ROUND_TIMEOUT = 900  # seconds (15 minutes); also the round lifetime
RECENT_PICKS = 20  # per channel: don't re-pick a servant within this many rounds

TITLES = {
    "guess_servant": "Guess the Servant",
    "guess_shadow": "Guess the Shadow",
    "guess_audio": "Guess the Voice",
    "guess_skill": "Guess by Skills",
}

WIN_REACTION = "\N{WHITE HEAVY CHECK MARK}"
WRONG_REACTION = "\N{CROSS MARK}"
# Cap wrong-but-real reactions to one per this many seconds per round, so a busy
# channel doesn't build a Discord reaction rate-limit backlog (which makes reactions
# and the reveal lag). Only bites under a flood; the winning reaction is unaffected.
WRONG_REACT_COOLDOWN = 1.5
FORFEIT_EMOTE = "\N{WAVING WHITE FLAG}\N{VARIATION SELECTOR-16}"  # vote-to-give-up react
FORFEIT_VOTES = 3  # distinct humans whose reaction ends the round
HINT_REWARD = (1.0, 0.7, 0.5, 0.3)  # win multiplier by hints used (0..3): hints cost QP
# Extra QP when the winner nails it on their FIRST guess -- i.e. made no earlier
# wrong-but-real guess of their own this round. A fraction of the (post-hint) award, so
# it scales with difficulty and rewards a clean no-hint solve the most.
FIRST_GUESS_BONUS = 0.5

# Post-reveal "next round" vote (config.next_vote_seconds > 0). Type-only for now.
NEXT_VOTE_TYPES = [
    ("guess_servant", "Servant"),
    ("guess_shadow", "Shadow"),
    ("guess_audio", "Voice"),
    ("guess_skill", "Skill"),
    ("random", "Random"),
]
# type value -> (cog class, whether its _play takes a positional difficulty). "random"
# is special-cased (GuessRandom._play takes difficulty as a keyword).
_VOTE_DISPATCH = {
    "guess_servant": ("GuessServant", True),
    "guess_shadow": ("GuessShadow", True),
    "guess_audio": ("GuessAudio", False),
    "guess_skill": ("GuessSkill", False),
}
NEXT_VOTE_TALLY_REFRESH = 2.0  # coalesce tally edits to this cadence, to spare the rate limit

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


def _refile(file: "discord.File | None") -> "tuple[bytes | None, str | None]":
    """Read a File's bytes up front so a fresh copy can be built for each send attempt:
    a discord.File is single-use and is closed after a send, so it can't be re-sent."""
    if file is None:
        return None, None
    return file.fp.read(), file.filename


async def _retry(action, what: str, *, tries: int = 2, delay: float = 2.0):
    """Await action() (a coroutine factory); retry on a transient Discord error (403 or
    5xx) -- a properly-permissioned channel occasionally 403s during a brief gateway or
    guild desync. Returns the result, or None on final failure (logged)."""
    for attempt in range(tries):
        try:
            return await action()
        except discord.HTTPException as e:
            if attempt + 1 < tries and getattr(e, "status", None) in (403, 500, 502, 503, 504):
                await asyncio.sleep(delay)
                continue
            log.warning("%s failed: %s", what, e)
            return None


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


class _ChannelStart:
    """A minimal stand-in for a discord.Interaction, so launch_round can start a round
    from a channel (the next-round vote) with no slash command behind it. It exposes only
    the surface launch_round touches; a vote-triggered start has nowhere to put an
    ephemeral reply, so rejections (game off, a round already running) are swallowed."""

    def __init__(self, bot, channel: discord.abc.Messageable) -> None:
        self.channel = channel
        self.channel_id = channel.id
        self.guild_id = channel.guild.id
        self.user = bot.user  # "started by" the bot, via the vote
        self.response = self._Response()
        self.followup = self._Followup(channel)

    class _Response:
        async def defer(self, *a, **k) -> None:
            return None

        async def send_message(self, *a, **k) -> None:
            return None  # nowhere to send an ephemeral rejection; abort quietly

    class _Followup:
        def __init__(self, channel: discord.abc.Messageable) -> None:
            self._channel = channel

        async def send(self, content=None, *, embed=None, file=None, view=None, **_k):
            kwargs: dict = {}
            if content is not None:
                kwargs["content"] = content
            if embed is not None:
                kwargs["embed"] = embed
            if file is not None:
                kwargs["file"] = file
            if view is not None:
                kwargs["view"] = view
            return await self._channel.send(**kwargs)


class PromptVoteView(discord.ui.View):
    """The 'vote for the next game' dropdown carried on a live round's prompt. Votes are
    recorded on the round (round.next_votes), so they survive prompt bumps and it doesn't
    matter which prompt copy is clicked. Each posted/bumped prompt gets its own instance."""

    def __init__(self, round_: "ChatRound") -> None:
        super().__init__(timeout=ROUND_TIMEOUT)
        self._round = round_
        select = discord.ui.Select(
            placeholder="Vote: next game...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=lbl, value=val, default=(val == round_.game_type))
                for val, lbl in round_.vote_options
            ],
        )
        select.callback = self._on_vote
        self._select = select
        self.add_item(select)

    async def _on_vote(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
        await self._round.record_next_vote(interaction.user.id, self._select.values[0])


class BeatView(discord.ui.View):
    """The short beat on a reveal before the next round auto-starts. The winner is already
    decided (by votes cast DURING the round), so Start now only skips the beat -- it can't
    change the outcome, which is exactly why a single press is fine and not a snipe."""

    def __init__(self, *, bot, channel, winner, difficulty, include_jp, seconds) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.channel = channel
        self.winner = winner
        self.difficulty = difficulty
        self.include_jp = include_jp
        self.seconds = seconds
        self.message: discord.Message | None = None
        self._done = False
        self._task: asyncio.Task | None = None
        start = discord.ui.Button(label="Start now", style=discord.ButtonStyle.primary)
        start.callback = self._on_start_now
        self.add_item(start)

    def attach(self, message: discord.Message) -> None:
        self.message = message
        self._task = asyncio.create_task(self._beat())

    async def _beat(self) -> None:
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            return
        await self._go()

    async def _on_start_now(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
        await self._go()

    async def _go(self) -> None:
        if self._done:
            return
        self._done = True
        if self._task is not None:
            self._task.cancel()
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        await self._launch()

    async def _launch(self) -> None:
        ctx = _ChannelStart(self.bot, self.channel)
        diff = (
            app_commands.Choice(name=self.difficulty.title(), value=self.difficulty)
            if self.difficulty
            else None
        )
        try:
            if self.winner == "random":
                cog = self.bot.get_cog("GuessRandom")
                if cog is not None:
                    await cog._play(ctx, include_jp=self.include_jp, difficulty=diff)
                return
            name, has_difficulty = _VOTE_DISPATCH[self.winner]
            cog = self.bot.get_cog(name)
            if cog is None:
                return
            if has_difficulty:
                await cog._play(
                    ctx, diff or app_commands.Choice(name="Easy", value="easy"),
                    include_jp=self.include_jp,
                )
            else:
                await cog._play(ctx, include_jp=self.include_jp)
        except Exception:
            log.exception("next-round launch failed (%s)", self.winner)
            try:
                await self.channel.send(
                    "Couldn't start the next round -- use a /guess command."
                )
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
        hints: "list[tuple[str, str]] | None" = None,
        game_type: str = "",
        difficulty: "str | None" = None,
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
        # A per-round hint sequence override (guess_skill supplies the three skill
        # names in a random order, then rarity, then class). None -> the default
        # rarity/gender/class computed in _hint_list.
        self._custom_hints = hints
        # This round's type + difficulty, so the reveal's next-round vote can pre-seed
        # "continue the same game" and re-launch it.
        self.game_type = game_type
        self.difficulty = difficulty
        # Votes for the NEXT game, cast on this round's prompt while it's live -- stored on
        # the round so they survive prompt bumps. vote_options is the eligible (value,label)
        # list, filled by launch_round from the guild config.
        self.next_votes: dict[int, str] = {}
        self.vote_options: list[tuple[str, str]] = []
        self._last_tally_edit = 0.0
        self.channel_id: int | None = None
        self.message: discord.Message | None = None
        self.expires_at: dt.datetime | None = None
        self.claimed = False
        self.hints_given = 0
        self._last_wrong_react = 0.0  # monotonic time of the last wrong-guess react
        self.wrong_guessers: set[int] = set()  # users who made a wrong-but-real guess
        self.vote_message: discord.Message | None = None
        self.vote_reactors: set[int] = set()
        self._timeout_task: asyncio.Task | None = None
        # EN rounds (include_jp False) ignore JP-only servant names typed in chat.
        self.include_jp = False
        # Reposting the prompt so it doesn't scroll away (config REPOST_AFTER). The
        # built prompt embed is kept so a repost is a faithful copy.
        self.prompt_embed: discord.Embed | None = None
        self.repost_after = 0
        self._msgs_since_prompt = 0

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

    def _hint_list(self) -> list[tuple[str, str]]:
        """Ordered hint sequence. A round may supply its own (guess_skill: skill names,
        then rarity, then class); otherwise the default rarity, gender, class (each only
        if known). Shared by give_hint and the prompt's revealed-hints field."""
        if self._custom_hints is not None:
            return self._custom_hints
        hints: list[tuple[str, str]] = []
        if self.servant.rarity:
            hints.append(("Rarity", f"{self.servant.rarity}-star"))
        if self.servant.gender in ("male", "female"):
            hints.append(("Gender", self.servant.gender.title()))
        if self.servant.class_name:
            hints.append(("Class", class_display(self.servant.class_name)))
        return hints

    async def give_hint(self, channel: discord.abc.Messageable) -> None:
        """On '@bot hint': reveal rarity, then gender, then class, on successive asks."""
        if self.claimed:
            return
        hints = self._hint_list()
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
        await self._refresh_prompt()  # surface the revealed hints on the prompt itself

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
            all_names=self.bot.servants.spaced_names(self.include_jp),
        ):
            await self._win(message)
        elif self.bot.servants.resembles_servant(
            message.content,
            extra=self.bot.aliases.all_terms(
                frozenset() if self.include_jp else self.bot.servants.jp_ids()
            ),
            include_jp=self.include_jp,
        ):
            # This player has now spent a guess, so they no longer qualify for the
            # first-guess bonus if they later get it right (recorded even when the react
            # below is throttled -- the guess still happened).
            self.wrong_guessers.add(message.author.id)
            # Throttle wrong-guess reacts so a busy channel doesn't back up the reaction
            # rate limit (which is what makes reactions and reveals lag).
            now = time.monotonic()
            if now - self._last_wrong_react >= WRONG_REACT_COOLDOWN:
                self._last_wrong_react = now
                await self._react(message, WRONG_REACTION)

    async def _win(self, message: discord.Message) -> None:
        self.claimed = True
        self._cancel_timeout()
        self._unregister()
        base = self._effective_points()
        # First-guess bonus: the winner made no wrong-but-real guess of their own before
        # nailing it. Scales with the (post-hint) award, floored at 1.
        first_try = message.author.id not in self.wrong_guessers
        bonus = max(1, round(base * FIRST_GUESS_BONUS)) if first_try else 0
        award = base + bonus
        total = await self.bot.scoring.award(message.guild.id, message.author.id, award)
        await self.bot.games.resolve(self.game_id, "win", message.author.id, award)
        # Post the FULL reveal at the bottom FIRST, where the action is -- nobody
        # backscrolls to the prompt -- tagging the winner with their reward. Before the
        # win reactions, so a busy channel's reaction backlog can't delay the reveal.
        praise = host.line(self.host_id, "correct", player=message.author.display_name)
        embed, file = await self._build_reveal_embed(f"*{praise}*")
        embed.color = discord.Color.green()
        reward = f"+{qp(award)} (QP {qp(total)})"
        extras = []
        if bonus:
            extras.append(f"first guess +{qp(bonus)}")
        if self.hints_given:
            extras.append(f"{self.hints_given} hint{'s' if self.hints_given > 1 else ''} used")
        if extras:
            reward += "  ·  " + "  ·  ".join(extras)
        embed.add_field(name="Reward", value=reward, inline=False)
        await self._post_reveal(message.channel, embed, file, ping=message.author.mention, engaged=True)
        # Slim the now-buried prompt so it isn't left showing "type the name".
        await self._slim_prompt(f"Solved by {message.author.display_name}.")
        await self._react(message, WIN_REACTION)
        await self._react(message, discord.PartialEmoji.from_str(qp_emote()))

    async def _post_reveal(
        self,
        channel: discord.abc.Messageable,
        embed: discord.Embed,
        file: discord.File | None,
        *,
        ping: str | None = None,
        engaged: bool = False,
    ) -> None:
        """Send a reveal at the bottom of the channel. With the next-round vote enabled,
        an engaged round (won/forfeited, or one that drew votes on its prompt) notes the
        winning next game on the reveal and starts it after a short beat -- Start now skips
        the beat, and can't change the (already-decided) winner. A quiet timeout with no
        votes just rests. With the vote off, the plain Play Again button is used instead."""
        beat = None
        vote_on = self.bot.config.next_vote_seconds > 0 and self.replay is not None
        if vote_on and (engaged or self.next_votes):
            winner = self._next_winner()
            label = dict(NEXT_VOTE_TYPES).get(winner, winner)
            embed.add_field(
                name="Next up",
                value=f"**{label}** in {self.bot.config.next_vote_seconds}s",
                inline=False,
            )
            beat = BeatView(
                bot=self.bot,
                channel=channel,
                winner=winner,
                difficulty=self.difficulty,
                include_jp=self.include_jp,
                seconds=self.bot.config.next_vote_seconds,
            )
        view = beat or (
            PlayAgainView(self.replay) if (self.replay is not None and not vote_on) else None
        )
        base: dict = {}
        if view is not None:
            base["view"] = view
        if ping is not None:
            base["content"] = ping
            base["allowed_mentions"] = _PING_USER
        data, filename = _refile(file)

        def send():
            kwargs = dict(base, embed=embed)
            if data is not None:
                kwargs["file"] = discord.File(io.BytesIO(data), filename=filename)
            return channel.send(**kwargs)

        sent = await _retry(send, f"reveal for game {self.game_id}")
        if sent is None:
            return
        if beat is not None:
            beat.attach(sent)
        elif view is not None:
            view.message = sent

    async def _slim_prompt(self, note: str) -> None:
        """Edit the now-buried prompt to a terminal state but KEEP its cropped image /
        audio clip, so players can scroll back and see what the round asked. The crop
        or mp3 is re-uploaded (attachment://) so it stays inside the embed; a remote-URL
        silhouette is just re-pointed and needs no attachment."""
        embed = discord.Embed(title=f"It was {self.servant.name}!", description=note)
        file = _attach(embed, self.prompt)
        # view=None clears the on-prompt next-game dropdown now that the round is over.
        kwargs: dict = {"embed": embed, "view": None}
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
        message_id = self.message.id
        atts = kwargs.pop("attachments", None)
        refiled = [_refile(a) for a in atts] if atts is not None else None

        def do():
            k = dict(kwargs)
            if refiled is not None:
                k["attachments"] = [
                    discord.File(io.BytesIO(d or b""), filename=fn) for d, fn in refiled
                ]
            return get_partial(message_id).edit(**k)

        await _retry(do, f"edit prompt for game {self.game_id}")

    def _render_prompt(self) -> "tuple[discord.Embed, discord.File | None]":
        """Current prompt embed (base + any revealed hints) with its media re-attached,
        for (re)posting or refreshing in place."""
        # deepcopy, not Embed.copy(): the latter shares the underlying fields list, so
        # add_field below would accumulate on prompt_embed -- stacking a new "Hints"
        # field on every hint/bump instead of replacing it.
        embed = copy.deepcopy(self.prompt_embed) if self.prompt_embed else discord.Embed()
        revealed = self._hint_list()[: self.hints_given]
        if revealed:
            embed.add_field(
                name="Hints",
                value="\n".join(f"{lbl}: **{val}**" for lbl, val in revealed),
                inline=False,
            )
        if self.next_votes and self.bot.config.next_vote_seconds > 0:
            embed.add_field(
                name="Next game vote", value=self._vote_tally_text(), inline=False
            )
        return embed, _attach(embed, self.prompt)

    def _make_prompt_vote_view(self) -> "PromptVoteView | None":
        if self.bot.config.next_vote_seconds > 0 and len(self.vote_options) >= 2:
            return PromptVoteView(self)
        return None

    def _next_winner(self) -> str:
        """Winning next-game type: most-voted, defaulting to this round's own type
        (continue), with ties broken toward continuing."""
        counts: dict[str, int] = {}
        for v in self.next_votes.values():
            counts[v] = counts.get(v, 0) + 1
        if not counts:
            return self.game_type
        top = max(counts.values())
        leaders = [val for val, _ in self.vote_options if counts.get(val, 0) == top]
        if self.game_type in leaders or not leaders:
            return self.game_type
        return leaders[0]

    def _vote_tally_text(self) -> str:
        counts: dict[str, int] = {}
        for v in self.next_votes.values():
            counts[v] = counts.get(v, 0) + 1
        parts = "   ".join(
            f"{lbl} {counts[val]}" for val, lbl in self.vote_options if counts.get(val)
        )
        lead = dict(NEXT_VOTE_TYPES).get(self._next_winner(), self._next_winner())
        return f"Leading: **{lead}**.\n{parts}" if parts else f"Leading: **{lead}**."

    async def record_next_vote(self, user_id: int, value: str) -> None:
        """Record a vote for the next game (cast on the prompt); throttled prompt refresh
        so a burst of votes doesn't hammer the edit rate limit."""
        if self.claimed:
            return
        self.next_votes[user_id] = value
        now = time.monotonic()
        if now - self._last_tally_edit >= NEXT_VOTE_TALLY_REFRESH:
            self._last_tally_edit = now
            await self._refresh_prompt()

    async def _refresh_prompt(self) -> None:
        """Edit the live prompt in place so a freshly revealed hint shows on it."""
        if self.prompt_embed is None:
            return
        embed, file = self._render_prompt()
        kwargs: dict = {"embed": embed}
        if file is not None:
            kwargs["attachments"] = [file]
        await self._edit_prompt(**kwargs)

    async def note_activity(self, channel: discord.abc.Messageable) -> None:
        """Count a channel message; once `repost_after` go by, repost the prompt at the
        bottom so players don't scroll up. No-op if disabled or the round is over."""
        if self.claimed or self.repost_after <= 0 or self.prompt_embed is None:
            return
        self._msgs_since_prompt += 1
        if self._msgs_since_prompt < self.repost_after:
            return
        self._msgs_since_prompt = 0
        await self._repost_prompt(channel)

    async def _repost_prompt(self, channel: discord.abc.Messageable) -> None:
        # Re-post the prompt (embed + revealed hints + re-attached crop/clip) at the
        # bottom. The old copy is left in place: deleting it spams servers' message-log
        # bots with "deleted message" entries.
        embed, file = self._render_prompt()
        data, filename = _refile(file)
        view = self._make_prompt_vote_view()

        def send():
            kwargs: dict = {"embed": embed}
            if data is not None:
                kwargs["file"] = discord.File(io.BytesIO(data), filename=filename)
            if view is not None:
                kwargs["view"] = view
            return channel.send(**kwargs)

        new_msg = await _retry(send, f"repost prompt for game {self.game_id}")
        if new_msg is None:
            return
        self.message = new_msg
        try:
            await self.bot.games.attach_message(self.game_id, new_msg.id)
        except Exception:
            log.exception("failed to reattach message for game %s", self.game_id)

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
        await self._post_reveal(channel, embed, file, engaged=True)
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
    async def _react(message: discord.Message, emoji: "str | discord.PartialEmoji") -> None:
        await _retry(
            lambda: message.add_reaction(emoji),
            f"reaction {str(emoji)!r} in channel {getattr(message.channel, 'id', '?')}",
        )

    def _also_accepted(self, limit: int = 12) -> str:
        """A short, readable list of the servant's OTHER accepted answers (curated +
        community aliases, plus any static NPC/JP ones), shown on the reveal so newcomers
        pick up the shortcuts the regulars already know. Shortest first, so the handy ones
        (dvr, cat, sono-g) surface; capped, with a '+N more' beyond the limit."""
        pool = list(self.bot.aliases.display_for(self.servant.id)) + list(self.servant.aliases)
        seen = {matching.normalize(self.servant.name)}
        out: list[str] = []
        for a in pool:
            norm = matching.normalize(a)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(a.strip())
        if not out:
            return ""
        out.sort(key=lambda a: (len(a), a.lower()))
        text = ", ".join(out[:limit])
        if len(out) > limit:
            text += f", (+{len(out) - limit} more)"
        return text

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
            embed.add_field(name="Class", value=class_display(s.class_name))
        if s.rarity:
            embed.add_field(name="Rarity", value=f"{s.rarity}-star")
        if s.cv:
            embed.add_field(name="CV", value=s.cv)
        also = self._also_accepted()
        if also:
            embed.add_field(name="Also accepted", value=also, inline=False)
        file = _attach(embed, media)
        return embed, file


def _next_vote_options(bot, cfg, channel_is_game: bool) -> "list[tuple[str, str]]":
    """Eligible next-game types for the on-prompt vote: games enabled in this guild plus
    Random (Shadow only with an asset host). Empty unless the vote is on and there are at
    least two choices to offer."""
    if bot.config.next_vote_seconds <= 0 or not channel_is_game:
        return []
    options: list[tuple[str, str]] = []
    for value, label in NEXT_VOTE_TYPES:
        if value == "random":
            options.append((value, label))
        elif value == "guess_shadow" and not bot.config.assets_base_url:
            continue
        elif bot.guild_config.game_enabled(cfg, value):
            options.append((value, label))
    return options if len(options) >= 2 else []


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
    include_jp: bool = False,
    filters_label: str | None = None,
    replay_override: "Callable[[discord.Interaction], Awaitable[bool]] | None" = None,
    build_hints: "Callable[[Servant], list[tuple[str, str]]] | None" = None,
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
            msg = (
                "No servants match those filters -- try removing one."
                if filters_label
                else "Couldn't start a round right now. Try again."
            )
            await interaction.followup.send(msg)
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
            # Re-run the same game (same difficulty/host/region) from Play Again. The
            # picker closure already carries the JP choice; pass include_jp too so the
            # replayed round's region tag matches the pool it actually draws from.
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
                include_jp=include_jp,
                filters_label=filters_label,
                build_hints=build_hints,
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
            # replay_override lets /guessrandom re-roll a fresh game on Play Again.
            replay=(replay_override or _replay) if channel_is_game else None,
            hints=build_hints(servant) if build_hints else None,
            game_type=game_type,
            difficulty=difficulty,
        )
        round_.expires_at = expires_at
        intro = host.line(host_id, "start", player=interaction.user.display_name)
        title = TITLES.get(game_type, "Guess the Servant")
        if difficulty:
            title = f"{title} - {difficulty.title()}"
        # Region tag so it's always clear which pool a round draws from: NA (default)
        # or JP (the *jp commands, which add JP-only servants on top of NA).
        title = f"{title} [{'JP' if include_jp else 'NA'}]"
        if game_type == "guess_audio":
            # The host is itself a Servant, so be explicit that the clip is the
            # Servant to identify -- otherwise players read the host's portrait as
            # the voice in the clip.
            how = (
                "**Listen to the clip and type the servant's name in chat.** "
                "The voice you hear is the mystery servant."
            )
        elif game_type == "guess_skill":
            how = (
                "**These are the servant's three skills.** Type the servant's name in "
                f"chat. Tag {bot.user.mention} with **hint** to reveal a skill name "
                "(then rarity, then class)."
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
        if filters_label:
            embed.add_field(name="Pool", value=filters_label, inline=False)
        prompt_file = _attach(embed, prompt)
        round_.prompt_embed = embed
        round_.repost_after = bot.config.repost_after
        round_.include_jp = include_jp
        round_.vote_options = _next_vote_options(bot, cfg, channel_is_game)
        kwargs = {"file": prompt_file} if prompt_file else {}
        vote_view = round_._make_prompt_vote_view()
        if vote_view is not None:
            kwargs["view"] = vote_view
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
        embed.add_field(name="Class", value=class_display(servant.class_name))
    if servant.rarity:
        embed.add_field(name="Rarity", value=f"{servant.rarity}-star")
    embed.add_field(name="Started by", value=interaction.user.mention)
    embed.add_field(
        name="Where", value=f"<#{interaction.channel_id}> ([jump]({message.jump_url}))"
    )
    data, filename = _refile(_attach(embed, prompt))

    def send():
        kwargs = {"embed": embed, "allowed_mentions": _NO_PINGS}
        if data is not None:
            kwargs["file"] = discord.File(io.BytesIO(data), filename=filename)
        return channel.send(**kwargs)

    await _retry(send, f"audit-log post to channel {channel_id}")
