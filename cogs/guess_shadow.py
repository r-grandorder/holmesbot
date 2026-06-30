from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import host, images

from . import filters
from .guess_base import Media, launch_round

# difficulty -> (crop size px or None for the full silhouette, points)
DIFFICULTY = {
    "easy": (None, 15),
    "medium": (220, 24),
    "hard": (150, 38),
    "lunatic": (90, 80),
}

_DIFF_CHOICES = [
    app_commands.Choice(name="Easy", value="easy"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="Hard", value="hard"),
    app_commands.Choice(name="Lunatic", value="lunatic"),
]


class GuessShadow(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _play(
        self,
        interaction: discord.Interaction,
        difficulty,
        *,
        include_jp: bool,
        klass,
        rarity,
        attribute,
        trait,
    ) -> None:
        base = self.bot.config.assets_base_url
        if not base:
            await interaction.response.send_message(
                "Shadow assets aren't set up yet.", ephemeral=True
            )
            return
        diff = difficulty.value if difficulty else "easy"
        crop_size, points = DIFFICULTY[diff]
        host_id = host.host_for("guess_shadow")
        filt, flabel = filters.from_params(klass, rarity, attribute, trait)

        def picker(allow):
            # Compose the restriction gate with region (JP only via *jp) and the
            # category filter. NPCs are never in the silhouette manifest.
            def gate(sid: int, asc: str) -> bool:
                if not allow(sid, asc):
                    return False
                s = self.bot.servants.get(sid)
                return (
                    bool(s)
                    and (include_jp or not s.jp)
                    and (filt is None or filt.matches(s))
                )

            pick = self.bot.shadows.pick(gate)
            if pick is None:
                return None
            servant_id, ascension = pick
            servant = self.bot.servants.get(servant_id)
            return (servant, ascension) if servant else None

        async def build_prompt(session, servant, ascension):
            url = f"{base}/shadow/v3/{servant.id}/{ascension}.png"
            if crop_size is None:  # easy: the whole silhouette, served straight from S3
                return Media(is_image=True, url=url)
            data = await images.fetch_bytes(session, url)
            png = images.crop_silhouette(data, crop_size)
            return Media(is_image=True, data=png, filename="prompt.png")

        async def build_reveal(_session, servant, ascension):
            return Media(is_image=True, url=f"{base}/figure/v3/{servant.id}/{ascension}.png")

        await launch_round(
            self,
            interaction,
            game_type="guess_shadow",
            host_id=host_id,
            points=points,
            picker=picker,
            build_prompt=build_prompt,
            build_reveal=build_reveal,
            difficulty=diff,
            include_jp=include_jp,
            filters_label=flabel,
        )

    @app_commands.command(
        name="guessshadow",
        description="Guess the servant from their silhouette.",
    )
    @app_commands.describe(difficulty="Higher difficulties crop the silhouette for more QP", **filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        difficulty=_DIFF_CHOICES,
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessshadow(
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
        name="guessshadowjp",
        description="Like /guessshadow, but the pool also includes JP-only servants.",
    )
    @app_commands.describe(difficulty="Higher difficulties crop the silhouette for more QP", **filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        difficulty=_DIFF_CHOICES,
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessshadowjp(
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
    await bot.add_cog(GuessShadow(bot))
