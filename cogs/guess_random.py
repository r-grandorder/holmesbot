from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

from . import filters

# (cog class name, whether its _play takes a difficulty arg). Voice has none.
_GAMES = (("GuessServant", True), ("GuessAudio", False), ("GuessShadow", True))
_DIFFS = ("easy", "medium", "hard", "lunatic")
_DIFF_CHOICES = [app_commands.Choice(name=d.title(), value=d) for d in _DIFFS]
_FILTER_CHANCE = 0.5   # ~half of rounds are a straight game (no filters)
_PER_DIM_CHANCE = 0.5  # within a filtered round, chance to roll each dimension
_MIN_POOL = 4          # re-roll a combo that matches fewer than this many servants


class GuessRandom(commands.Cog):
    """A surprise round: a random game (art/shadow/voice), sometimes with random
    category filters. Play Again re-rolls a fresh game each time. An optional
    difficulty pins the art/shadow rounds it picks; otherwise difficulty is random."""

    def __init__(self, bot) -> None:
        self.bot = bot

    def _roll_filters(self, include_jp: bool):
        """Maybe roll a random filter combo. Returns (klass, rarity, attribute, trait)
        as Choice objects or None -- the shape the game _play methods expect."""
        none = (None, None, None, None)
        if random.random() >= _FILTER_CHANCE:
            return none
        pick = lambda choices: random.choice(choices) if random.random() < _PER_DIM_CHANCE else None
        for _ in range(8):  # keep rolling until the combo has a playable pool
            combo = (
                pick(filters.CLASS_CHOICES),
                pick(filters.RARITY_CHOICES),
                pick(filters.ATTRIBUTE_CHOICES),
                pick(filters.TRAIT_CHOICES),
            )
            filt, _label = filters.from_params(*combo)
            if filt is None:  # rolled nothing this time -> straight game
                return none
            if self.bot.servants.count_matching(filt, include_jp) >= _MIN_POOL:
                return combo
        return none  # nothing playable rolled -> fall back to a straight game

    async def _play(
        self,
        interaction: discord.Interaction,
        *,
        include_jp: bool,
        difficulty=None,
    ) -> bool:
        # Don't roll shadow unless its S3 assets are configured.
        games = [
            g for g in _GAMES
            if g[0] != "GuessShadow" or self.bot.config.assets_base_url
        ]
        cog_name, has_difficulty = random.choice(games)
        cog = self.bot.get_cog(cog_name)
        if cog is None:
            await interaction.response.send_message(
                "Games aren't loaded right now.", ephemeral=True
            )
            return False
        klass, rarity, attribute, trait = self._roll_filters(include_jp)

        async def reroll(again: discord.Interaction) -> bool:
            # Play Again rolls a fresh game; the chosen difficulty (if any) carries over.
            return await self._play(again, include_jp=include_jp, difficulty=difficulty)

        if has_difficulty:
            if difficulty:
                diff_choice = difficulty
            else:
                d = random.choice(_DIFFS)
                diff_choice = app_commands.Choice(name=d, value=d)
            return await cog._play(
                interaction, diff_choice, include_jp=include_jp,
                klass=klass, rarity=rarity, attribute=attribute, trait=trait,
                replay_override=reroll,
            )
        return await cog._play(
            interaction, include_jp=include_jp,
            klass=klass, rarity=rarity, attribute=attribute, trait=trait,
            replay_override=reroll,
        )

    @app_commands.command(
        name="guessrandom",
        description="Surprise round: a random game, sometimes with random filters.",
    )
    @app_commands.describe(difficulty="Difficulty for the art/shadow rounds it picks (random if unset)")
    @app_commands.choices(difficulty=_DIFF_CHOICES)
    async def guessrandom(
        self,
        interaction: discord.Interaction,
        difficulty: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._play(interaction, include_jp=False, difficulty=difficulty)

    @app_commands.command(
        name="guessrandomjp",
        description="Like /guessrandom, but JP-only servants can appear too.",
    )
    @app_commands.describe(difficulty="Difficulty for the art/shadow rounds it picks (random if unset)")
    @app_commands.choices(difficulty=_DIFF_CHOICES)
    async def guessrandomjp(
        self,
        interaction: discord.Interaction,
        difficulty: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._play(interaction, include_jp=True, difficulty=difficulty)


async def setup(bot) -> None:
    await bot.add_cog(GuessRandom(bot))
