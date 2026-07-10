from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import host, images

from .guess_base import Media, launch_round

# difficulty -> (crop size px or None for the whole CE, points). Smaller crop = harder = bigger
# reward. CE tiers still out-reward servant (10/18/30/150), but trimmed a bit since CE art is
# reverse-searchable (easy shows the whole thing); lunatic CE ~2x lunatic servant. Lunatic also
# grayscales + scrambles the slice. Tunable.
DIFFICULTY = {
    "easy": (None, 40),
    "medium": (280, 80),
    "hard": (140, 160),
    "lunatic": (90, 320),
}

_DIFF_CHOICES = [
    app_commands.Choice(name="Easy", value="easy"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="Hard", value="hard"),
    app_commands.Choice(name="Lunatic", value="lunatic"),
]


class GuessCe(commands.Cog):
    """Guess the Craft Essence from a cropped slice of its illustration. Reuses the whole
    guessing framework -- a CraftEssence duck-types the Servant fields launch_round touches."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def _play(
        self,
        interaction: discord.Interaction,
        difficulty,
        *,
        include_jp: bool = False,
        replay_override=None,
    ) -> bool:
        diff = difficulty.value if difficulty else "easy"
        size, points = DIFFICULTY[diff]
        lunatic = diff == "lunatic"
        host_id = host.host_for("guess_ce", diff)

        def picker(allow):
            return self.bot.ces.pick(allow=allow)

        async def build_prompt(session, ce, key):
            data = await images.fetch_bytes(session, ce.art[key])
            if size is None:  # easy: show the whole Craft Essence, trimmed like the reveal
                png = images.trim_to_content(data)
            else:
                png = images.crop_random(data, size, grayscale=lunatic, scramble=lunatic)
            return Media(is_image=True, data=png, filename="prompt.png")

        async def build_reveal(session, ce, key):
            data = await images.fetch_bytes(session, ce.art[key])
            return Media(is_image=True, data=images.trim_to_content(data), filename="reveal.png")

        return await launch_round(
            self,
            interaction,
            game_type="guess_ce",
            host_id=host_id,
            points=points,
            picker=picker,
            build_prompt=build_prompt,
            build_reveal=build_reveal,
            difficulty=diff,
            include_jp=include_jp,
            replay_override=replay_override,
        )

    @app_commands.command(
        name="guessce", description="Guess the Craft Essence from a cropped slice of its art."
    )
    @app_commands.describe(difficulty="Smaller crop, bigger reward, tougher pull")
    @app_commands.choices(difficulty=_DIFF_CHOICES)
    @app_commands.guild_only()
    async def guessce(
        self,
        interaction: discord.Interaction,
        difficulty: "app_commands.Choice[str] | None" = None,
    ) -> None:
        await self._play(interaction, difficulty)


async def setup(bot) -> None:
    await bot.add_cog(GuessCe(bot))
