from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import host, images

from .guess_base import Media, launch_round

# difficulty -> (crop size px, points). Each difficulty also picks a different host.
DIFFICULTY = {
    "easy": (200, 10),
    "medium": (130, 18),
    "hard": (90, 30),
    "lunatic": (70, 70),
}


class GuessServant(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="guessservant",
        description="Guess the servant from a cropped slice of their art.",
    )
    @app_commands.describe(difficulty="Smaller crop, bigger reward, tougher host")
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name="Easy", value="easy"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Hard", value="hard"),
            app_commands.Choice(name="Lunatic", value="lunatic"),
        ]
    )
    async def guessservant(
        self,
        interaction: discord.Interaction,
        difficulty: app_commands.Choice[str] | None = None,
    ) -> None:
        diff = difficulty.value if difficulty else "easy"
        size, points = DIFFICULTY[diff]
        lunatic = diff == "lunatic"
        host_id = host.host_for("guess_servant", diff)

        def picker(allow):
            return self.bot.servants.pick(asset="art", allow=allow)

        async def build_prompt(session, servant, ascension):
            data = await images.fetch_bytes(session, servant.art[ascension])
            png = images.crop_random(data, size, grayscale=lunatic, scramble=lunatic)
            return Media(is_image=True, data=png, filename="prompt.png")

        async def build_reveal(session, servant, ascension):
            data = await images.fetch_bytes(session, servant.art[ascension])
            return Media(is_image=True, data=images.trim_to_content(data), filename="reveal.png")

        await launch_round(
            self,
            interaction,
            game_type="guess_servant",
            host_id=host_id,
            points=points,
            picker=picker,
            build_prompt=build_prompt,
            build_reveal=build_reveal,
            difficulty=diff,
        )


async def setup(bot) -> None:
    await bot.add_cog(GuessServant(bot))
