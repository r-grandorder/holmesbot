from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

from data import host, images
from data.servants import class_display

from . import filters
from .guess_base import Media, launch_round

# A near-lunatic flat reward: identifying a servant from three generic, widely-shared
# skill icons (with no names) is about as hard as the toughest crops, so an icons-only
# solve pays like a jackpot. Each hint -- a skill name, then rarity, then class --
# trims the payout via the shared HINT_REWARD multipliers.
POINTS = 120


def _skill_hints(servant) -> list[tuple[str, str]]:
    """The hint sequence for a skill round: the three skill names in a random order
    (each labelled by its in-game slot so it maps to an icon in the strip), then
    rarity, then class. The order is fixed per round -- this is called once at launch."""
    slots = list(servant.skills)  # (num, name, icon) tuples, in slot order
    order = list(range(len(slots)))
    random.shuffle(order)
    hints = [(f"Skill {slots[i][0]}", slots[i][1]) for i in order]
    if servant.rarity:
        hints.append(("Rarity", f"{servant.rarity}-star"))
    if servant.class_name:
        hints.append(("Class", class_display(servant.class_name)))
    return hints


class GuessSkill(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _play(
        self,
        interaction: discord.Interaction,
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
        host_id = host.host_for("guess_skill")
        if filt_override is not None:
            filt, flabel = filt_override, flabel_override
        else:
            filt, flabel = filters.from_params(klass, rarity, attribute, trait)

        def picker(allow):
            # need_skills keeps only servants with a full 3-skill kit (which also drops
            # the NPC bosses, who carry no skills). The art restriction still applies to
            # the reveal ascension via `allow`.
            return self.bot.servants.pick(
                asset="art", allow=allow, include_jp=include_jp, filt=filt, need_skills=True
            )

        async def build_prompt(session, servant, ascension):
            icons = [
                await images.fetch_bytes(session, url)
                for (_num, _name, url) in servant.skills[:3]
            ]
            return Media(
                is_image=True, data=images.skill_strip(icons), filename="prompt.png"
            )

        async def build_reveal(session, servant, ascension):
            data = await images.fetch_bytes(session, servant.art[ascension])
            return Media(
                is_image=True, data=images.trim_to_content(data), filename="reveal.png"
            )

        return await launch_round(
            self,
            interaction,
            game_type="guess_skill",
            host_id=host_id,
            points=POINTS,
            picker=picker,
            build_prompt=build_prompt,
            build_reveal=build_reveal,
            include_jp=include_jp,
            filters_label=flabel,
            replay_override=replay_override,
            build_hints=_skill_hints,
        )

    @app_commands.command(
        name="guessskill",
        description="Guess the servant from their three skill icons.",
    )
    @app_commands.describe(**filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessskill(
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
        name="guessskilljp",
        description="Like /guessskill, but the pool also includes JP-only servants.",
    )
    @app_commands.describe(**filters.DESCRIBE)
    @app_commands.rename(klass="class")
    @app_commands.choices(
        klass=filters.CLASS_CHOICES,
        rarity=filters.RARITY_CHOICES,
        attribute=filters.ATTRIBUTE_CHOICES,
        trait=filters.TRAIT_CHOICES,
    )
    async def guessskilljp(
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
    await bot.add_cog(GuessSkill(bot))
