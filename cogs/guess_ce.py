from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from data import host, images

from .guess_base import Media, launch_round

# difficulty -> (crop size px or None for the whole CE, points). Smaller crop = harder = bigger
# reward. CE is niche/hard (lunatic ~3x lunatic servant's 150), so base rewards run high; the
# name hint below drops the payout via HINT_REWARD for players who need it. Lunatic also
# grayscales + scrambles the slice. Tunable.
DIFFICULTY = {
    "easy": (None, 100),
    "medium": (280, 180),
    "hard": (140, 240),
    "lunatic": (90, 500),
}

_DIFF_CHOICES = [
    app_commands.Choice(name="Easy", value="easy"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="Hard", value="hard"),
    app_commands.Choice(name="Lunatic", value="lunatic"),
]


def _prefix(name: str, frac: float) -> str:
    """The first `frac` of the name, trimmed, marked as partial."""
    cut = max(1, round(len(name) * frac))
    return name[:cut].rstrip() + "..."


def _ce_hints(ce) -> "list[tuple[str, str]]":
    """CE hints are all name-based: rarity is useless (every CE is 5-star) and CEs carry no
    class/gender. Progressive reveals -- masked initials (word count + lengths + first letters),
    then a growing prefix of the name -- each drops the win via HINT_REWARD (0.7/0.5/0.3), so a
    name reveal is decently penalized."""
    name = ce.name
    initials = " ".join(w[0] + "-" * (len(w) - 1) for w in name.split())
    return [
        ("Name shape", initials),
        ("Partial name", _prefix(name, 0.5)),
        ("Almost there", _prefix(name, 0.75)),
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
            build_hints=_ce_hints,
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
