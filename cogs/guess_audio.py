from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import audio, host, images

from . import filters
from .guess_base import Media, launch_round

POINTS = 20


class GuessAudio(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _play(
        self,
        interaction: discord.Interaction,
        *,
        include_jp: bool,
        klass,
        rarity,
        attribute,
        trait,
    ) -> None:
        host_id = host.host_for("guess_audio")
        filt, flabel = filters.from_params(klass, rarity, attribute, trait)

        def picker(allow):
            # Voice ignores the art restriction for inclusion (the challenge is
            # audio); the reveal still uses a non-restricted ascension, or no art.
            return self.bot.servants.pick_for_voice(allow, include_jp=include_jp, filt=filt)

        async def build_prompt(session, servant, ascension):
            # JP-only servants' voice lines live on the JP endpoint, not NA.
            region = "JP" if servant.jp else "NA"
            clip = await audio.fetch_voice_clip(session, servant.id, region=region)
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
            include_jp=include_jp,
            filters_label=flabel,
        )

    @app_commands.command(
        name="guessvoice",
        description="Guess the servant from one of their voice lines.",
    )
    @app_commands.describe(**filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessvoice(
        self,
        interaction: discord.Interaction,
        klass: app_commands.Choice[str] | None = None,
        rarity: app_commands.Choice[int] | None = None,
        attribute: app_commands.Choice[str] | None = None,
        trait: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._play(
            interaction, include_jp=False,
            klass=klass, rarity=rarity, attribute=attribute, trait=trait,
        )

    @app_commands.command(
        name="guessvoicejp",
        description="Like /guessvoice, but the pool also includes JP-only servants.",
    )
    @app_commands.describe(**filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessvoicejp(
        self,
        interaction: discord.Interaction,
        klass: app_commands.Choice[str] | None = None,
        rarity: app_commands.Choice[int] | None = None,
        attribute: app_commands.Choice[str] | None = None,
        trait: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._play(
            interaction, include_jp=True,
            klass=klass, rarity=rarity, attribute=attribute, trait=trait,
        )


async def setup(bot) -> None:
    await bot.add_cog(GuessAudio(bot))
