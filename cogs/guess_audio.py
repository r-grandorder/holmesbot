from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import audio, host, images

from .guess_base import Media, launch_round

POINTS = 20


class GuessAudio(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="guessvoice",
        description="Guess the servant from one of their voice lines.",
    )
    async def guessvoice(self, interaction: discord.Interaction) -> None:
        host_id = host.host_for("guess_audio")

        def picker(allow):
            # Voice ignores the art restriction for inclusion (the challenge is
            # audio); the reveal still uses a non-restricted ascension, or no art.
            return self.bot.servants.pick_for_voice(allow)

        async def build_prompt(session, servant, ascension):
            clip = await audio.fetch_voice_clip(session, servant.id)
            if clip is None:
                raise RuntimeError(f"servant {servant.id} has no voice lines")
            return Media(is_image=False, data=clip, filename="voice.mp3")

        async def build_reveal(session, servant, ascension):
            if not ascension:  # every ascension's art is restricted -> no image
                return None
            data = await images.fetch_bytes(session, servant.art[ascension])
            return Media(is_image=True, data=images.trim_to_content(data), filename="reveal.png")

        await launch_round(
            self,
            interaction,
            game_type="guess_audio",
            host_id=host_id,
            points=POINTS,
            picker=picker,
            build_prompt=build_prompt,
            build_reveal=build_reveal,
        )


async def setup(bot) -> None:
    await bot.add_cog(GuessAudio(bot))
