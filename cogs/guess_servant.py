from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import host, images

from . import filters
from .guess_base import Media, launch_round

# difficulty -> (crop size px, points). Each difficulty also picks a different host.
DIFFICULTY = {
    "easy": (200, 10),
    "medium": (130, 18),
    "hard": (90, 30),
    "lunatic": (70, 150),
}

_DIFF_CHOICES = [
    app_commands.Choice(name="Easy", value="easy"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="Hard", value="hard"),
    app_commands.Choice(name="Lunatic", value="lunatic"),
]


class GuessServant(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _play(
        self,
        interaction: discord.Interaction,
        difficulty,
        *,
        include_jp: bool,
        klass=None,
        rarity=None,
        attribute=None,
        trait=None,
        replay_override=None,
        filt_override=None,
        flabel_override=None,
    ) -> bool:
        diff = difficulty.value if difficulty else "easy"
        size, points = DIFFICULTY[diff]
        lunatic = diff == "lunatic"
        host_id = host.host_for("guess_servant", diff)
        if filt_override is not None:
            filt, flabel = filt_override, flabel_override
        else:
            filt, flabel = filters.from_params(klass, rarity, attribute, trait)

        def picker(allow):
            return self.bot.servants.pick(
                asset="art", allow=allow, include_jp=include_jp, filt=filt
            )

        async def build_prompt(session, servant, ascension):
            data = await images.fetch_bytes(session, servant.art[ascension])
            png = images.crop_random(data, size, grayscale=lunatic, scramble=lunatic)
            return Media(is_image=True, data=png, filename="prompt.png")

        async def build_reveal(session, servant, ascension):
            data = await images.fetch_bytes(session, servant.art[ascension])
            return Media(is_image=True, data=images.trim_to_content(data), filename="reveal.png")

        return await launch_round(
            self,
            interaction,
            game_type="guess_servant",
            host_id=host_id,
            points=points,
            picker=picker,
            build_prompt=build_prompt,
            build_reveal=build_reveal,
            difficulty=diff,
            include_jp=include_jp,
            filters_label=flabel,
            replay_override=replay_override,
        )

    @app_commands.command(
        name="guessservant",
        description="Guess the servant from a cropped slice of their art.",
    )
    @app_commands.describe(difficulty="Smaller crop, bigger reward, tougher host", **filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        difficulty=_DIFF_CHOICES,
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessservant(
        self,
        interaction: discord.Interaction,
        difficulty: app_commands.Choice[str] | None = None,
        klass: app_commands.Choice[str] | None = None,
        rarity: app_commands.Choice[int] | None = None,
        attribute: app_commands.Choice[str] | None = None,
        trait: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._play(
            interaction, difficulty, include_jp=False,
            klass=klass, rarity=rarity, attribute=attribute, trait=trait,
        )

    @app_commands.command(
        name="guessservantjp",
        description="Like /guessservant, but the pool also includes JP-only servants.",
    )
    @app_commands.describe(difficulty="Smaller crop, bigger reward, tougher host", **filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        difficulty=_DIFF_CHOICES,
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessservantjp(
        self,
        interaction: discord.Interaction,
        difficulty: app_commands.Choice[str] | None = None,
        klass: app_commands.Choice[str] | None = None,
        rarity: app_commands.Choice[int] | None = None,
        attribute: app_commands.Choice[str] | None = None,
        trait: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._play(
            interaction, difficulty, include_jp=True,
            klass=klass, rarity=rarity, attribute=attribute, trait=trait,
        )


async def setup(bot) -> None:
    await bot.add_cog(GuessServant(bot))
