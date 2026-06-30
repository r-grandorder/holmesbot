from __future__ import annotations

import discord
from discord.ext import commands

from .guess_base import FORFEIT_EMOTE, tidy_old_prompt

_HINT = ("hint", "help", "halp")
_GIVE_UP = ("give up", "giveup", "forfeit", "surrender")


def _emoji_key(emoji: object) -> str:
    # drop the variation selector so "flag" and "flag+VS16" compare equal
    return str(emoji).replace("️", "")


class ChatGuess(commands.Cog):
    """Routes plain channel messages to the active round in that channel, so
    players guess by typing the servant's name (no button/modal). Also handles
    '@bot hint' / '@bot give up' and counts give-up vote reactions. Requires the
    Message Content privileged intent."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._orphans_swept = False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content:
            return
        round_ = self.bot.active_rounds.get(message.channel.id)
        if round_ is None:
            return
        if self.bot.user is not None and self.bot.user in message.mentions:
            await self._handle_mention(message, round_)
        else:
            await round_.handle_message(message)
        # Bump the prompt to the bottom after enough channel chatter (REPOST_AFTER).
        await round_.note_activity(message.channel)

    async def _handle_mention(self, message: discord.Message, round_) -> None:
        # The Holmes @mention persona is disabled for now (kept dormant in
        # data/persona.py). Only the in-round game functions remain.
        text = message.content.lower()
        if any(k in text for k in _HINT):
            await round_.give_hint(message.channel)
        elif any(k in text for k in _GIVE_UP):
            await round_.start_forfeit_vote(message.channel)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user is None or payload.user_id == self.bot.user.id:
            return
        round_ = self.bot.forfeit_votes.get(payload.message_id)
        if round_ is None or round_.claimed:
            return
        if _emoji_key(payload.emoji) != _emoji_key(FORFEIT_EMOTE):
            return
        if await round_.register_vote(payload.user_id):
            self.bot.forfeit_votes.pop(payload.message_id, None)
            channel = self.bot.get_channel(payload.channel_id)
            if channel is not None:
                await round_.forfeit(channel)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user is None or payload.user_id == self.bot.user.id:
            return
        round_ = self.bot.forfeit_votes.get(payload.message_id)
        if round_ is None or round_.claimed:
            return
        if _emoji_key(payload.emoji) != _emoji_key(FORFEIT_EMOTE):
            return
        await round_.withdraw_vote(payload.user_id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # A round whose in-memory handler/timer died on a restart would otherwise
        # sit forever showing "type the name". Tidy those once on first ready (this
        # process holds the gateway lock, so only the leader sweeps).
        if self._orphans_swept:
            return
        self._orphans_swept = True
        for row in await self.bot.games.close_all_active():
            await tidy_old_prompt(
                self.bot, row["channel_id"], row["message_id"], row["answer_name"]
            )


async def setup(bot) -> None:
    await bot.add_cog(ChatGuess(bot))
