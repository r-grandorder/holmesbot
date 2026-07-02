from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

from data.servants import ServantFilter

from . import filters

# (cog class name, whether its _play takes a difficulty arg). Voice and skills have none.
_GAMES = (
    ("GuessServant", True),
    ("GuessAudio", False),
    ("GuessShadow", True),
    ("GuessSkill", False),
)
_DIFFS = ("easy", "medium", "hard", "lunatic")
_DIFF_CHOICES = [app_commands.Choice(name=d.title(), value=d) for d in _DIFFS]
_FILTER_CHANCE = 0.5   # ~half of rounds are a straight game (no pool)
_PER_DIM_CHANCE = 0.5  # within a pooled round, chance to roll each dimension
_MIN_POOL = 4          # re-roll a pool that matches fewer than this many servants


class GuessRandom(commands.Cog):
    """A surprise round: a random game (art/shadow/voice/skills), sometimes with a random
    category pool. Play Again re-rolls a fresh game. An optional difficulty pins the
    art/shadow rounds it picks. Class and rarity pools are rolled as multi-value sets
    (e.g. "Saber/Archer", "4-star/5-star") so showing the pool never gives away the
    single-value Class/Rarity hints; attribute/trait pools are single themed values."""

    def __init__(self, bot) -> None:
        self.bot = bot

    def _roll_filters(self, include_jp: bool):
        """Maybe roll a random pool. Returns (ServantFilter, label) or (None, None)."""
        if random.random() >= _FILTER_CHANCE:
            return None, None
        roll = lambda: random.random() < _PER_DIM_CHANCE
        for _ in range(8):  # keep rolling until the pool has enough servants
            filt = ServantFilter(
                class_names=frozenset(
                    c.value for c in random.sample(filters.CLASS_CHOICES, random.randint(2, 4))
                ) if roll() else frozenset(),
                rarities=frozenset(
                    c.value for c in random.sample(filters.RARITY_CHOICES, random.randint(2, 3))
                ) if roll() else frozenset(),
                attributes=frozenset([random.choice(filters.ATTRIBUTE_CHOICES).value]) if roll() else frozenset(),
                traits=frozenset([random.choice(filters.TRAIT_CHOICES).value]) if roll() else frozenset(),
            )
            if not filt.active:
                return None, None
            if self.bot.servants.count_matching(filt, include_jp) >= _MIN_POOL:
                return filt, filters.label_for(filt)
        return None, None

    async def _play(
        self,
        interaction: discord.Interaction,
        *,
        include_jp: bool,
        difficulty=None,
    ) -> bool:
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
        filt, flabel = self._roll_filters(include_jp)

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
                filt_override=filt, flabel_override=flabel, replay_override=reroll,
            )
        return await cog._play(
            interaction, include_jp=include_jp,
            filt_override=filt, flabel_override=flabel, replay_override=reroll,
        )

    @app_commands.command(
        name="guessrandom",
        description="Surprise round: a random game, sometimes with a random pool.",
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
