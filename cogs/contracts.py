from __future__ import annotations

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from branding import qp
from data import contract_game
from data.servants import class_display

from . import filters

log = logging.getLogger(__name__)

_RARITY_COLOR = {
    5: discord.Color.gold(),
    4: discord.Color.purple(),
    3: discord.Color.blue(),
    2: discord.Color.light_grey(),
    1: discord.Color.dark_grey(),
    0: discord.Color.dark_grey(),
}
_DENY = "The servant contract feature isn't open to you yet."
# The summon buttons' idle timeout. It RESETS on each click, so they stay live while you
# keep deciding, then grey out. Capped in practice by Discord's ~15min ephemeral token.
SUMMON_VIEW_TIMEOUT = 780


def _stars(rarity: int) -> str:
    return f"{rarity}\N{BLACK STAR}"


class ContractsCog(commands.Cog):
    """The contracted-servant QP sink. Gated by config.contract_whitelist: bot.py only loads
    this cog when the whitelist is non-empty, and every entrypoint re-checks membership so
    only testers can use it (everyone else is silently ignored)."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._xp_cd: dict[tuple[int, int], float] = {}  # (guild,user) -> last xp monotonic
        self._drop_cd: dict[int, float] = {}            # guild -> last grail-drop monotonic

    def _allowed(self, user_id: int) -> bool:
        return user_id in self.bot.config.contract_whitelist

    @staticmethod
    def _summon_title(servant, is_new: bool) -> str:
        return f"Summoned: {servant.name}" + (" (NEW!)" if is_new else "")

    def _servant_embed(self, servant, level: int, *, title=None, note=None, qp_line=None) -> discord.Embed:
        embed = discord.Embed(
            title=title or servant.name,
            description=note,
            color=_RARITY_COLOR.get(servant.rarity, discord.Color.blurple()),
        )
        if servant.face:
            embed.set_thumbnail(url=servant.face)
        art = contract_game.display_art(servant)
        if art:
            embed.set_image(url=art)
        embed.add_field(name="Class", value=class_display(servant.class_name) or "?")
        embed.add_field(name="Rarity", value=_stars(servant.rarity))
        embed.add_field(name="Power", value=f"{contract_game.power(servant, level):,}")
        line = getattr(servant, "summon_line", None)  # optional flavor (sync data addition)
        if line:
            embed.add_field(name="​", value=f"*{line}*", inline=False)
        if qp_line:
            embed.add_field(name="QP", value=qp_line, inline=False)
        return embed

    # ---- commands ----
    @app_commands.command(name="summon", description="Spend QP to summon a servant to contract.")
    @app_commands.guild_only()
    async def summon(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        cost = self.bot.config.contract_summon_cost
        bal = await self.bot.scoring.get_balance(interaction.guild_id, interaction.user.id)
        if bal < cost:
            return await interaction.response.send_message(
                f"You need {qp(cost)} to summon; you have {qp(bal)}.", ephemeral=True
            )
        new_bal = await self.bot.scoring.sub_qp(interaction.guild_id, interaction.user.id, cost)
        servant = contract_game.roll_servant(self.bot.servants)
        is_new = not await self.bot.contracts.has_contract(
            interaction.guild_id, interaction.user.id, servant.id
        )
        view = SummonView(self, interaction.user.id, servant)
        view.interaction = interaction
        await interaction.response.send_message(
            embed=self._servant_embed(
                servant, 1, title=self._summon_title(servant, is_new),
                qp_line=f"Spent {qp(cost)} · {qp(new_bal)} left",
            ),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="profile", description="View your (or another player's) contracted servant.")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose servant to view (defaults to you)")
    async def profile(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        target = member or interaction.user
        row = await self.bot.contracts.active(interaction.guild_id, target.id)
        if row is None:
            own = target.id == interaction.user.id
            return await interaction.response.send_message(
                "You have no active contract -- use /summon."
                if own
                else f"{target.display_name} has no active contract.",
                ephemeral=True,
            )
        servant = self.bot.servants.get(row["servant_id"])
        if servant is None:
            return await interaction.response.send_message(
                "That contracted servant is unavailable right now.", ephemeral=True
            )
        cap = contract_game.level_cap(row["grails_used"])
        grails = await self.bot.contracts.grail_balance(interaction.guild_id, target.id)
        embed = self._servant_embed(servant, row["level"], title=f"{target.display_name}'s Servant")
        embed.add_field(name="Level", value=f"{row['level']} / {cap}")
        embed.add_field(name="Grails", value=str(grails))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="grail", description="Spend a grail to raise your servant's level cap.")
    @app_commands.guild_only()
    async def grail(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        status, cap = await self.bot.contracts.apply_grail(interaction.guild_id, interaction.user.id)
        msg = {
            "no_contract": "You have no active contract. Use /summon first.",
            "not_max": f"Your servant must be at its cap (level {cap}) to use a grail.",
            "no_grails": "You have no grails. Claim them from chat drops.",
            "ok": f"Grail used -- your servant's cap is now **{cap}**.",
        }[status]
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="servantboard", description="Top contracted servants by level.")
    @app_commands.guild_only()
    @app_commands.describe(klass="Only show servants of this class")
    @app_commands.rename(klass="class")
    @app_commands.choices(klass=filters.CLASS_CHOICES)
    async def servantboard(
        self, interaction: discord.Interaction, klass: app_commands.Choice[str] | None = None
    ) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        rows = await self.bot.contracts.board(interaction.guild_id)
        if klass is not None:
            rows = [
                r for r in rows
                if (s := self.bot.servants.get(r["servant_id"])) and s.class_name.lower() == klass.value
            ]
        rows = rows[:10]
        if not rows:
            return await interaction.response.send_message("No contracts yet.", ephemeral=True)
        lines = []
        for i, r in enumerate(rows, 1):
            s = self.bot.servants.get(r["servant_id"])
            name = s.name if s else f"#{r['servant_id']}"
            cap = contract_game.level_cap(r["grails_used"])
            lines.append(f"**{i}.** <@{r['user_id']}> - {name} (Lv {r['level']}/{cap})")
        title = "Servant Leaderboard" + (f" - {klass.name}" if klass else "")
        await interaction.response.send_message(
            embed=discord.Embed(title=title, description="\n".join(lines)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ---- passive: XP + grail drops from chatting ----
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content:
            return
        if not self._allowed(message.author.id):
            return
        await self._grant_xp(message)
        await self._maybe_drop_grail(message)

    async def _grant_xp(self, message: discord.Message) -> None:
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._xp_cd.get(key, 0.0) < contract_game.XP_COOLDOWN:
            return
        self._xp_cd[key] = now
        result = await self.bot.contracts.add_xp(
            message.guild.id, message.author.id, contract_game.XP_PER_MSG
        )
        if not result:
            return
        servant_id, old_level, new_level, cap = result
        if new_level <= old_level:
            return
        servant = self.bot.servants.get(servant_id)
        name = servant.name if servant else "Your servant"
        tail = "  (at cap -- /grail to raise it)" if new_level >= cap else ""
        try:
            await message.channel.send(
                f"{message.author.display_name}'s **{name}** reached level **{new_level}**!{tail}",
                delete_after=12,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    async def _maybe_drop_grail(self, message: discord.Message) -> None:
        gid = message.guild.id
        now = time.monotonic()
        if now - self._drop_cd.get(gid, 0.0) < contract_game.GRAIL_DROP_COOLDOWN:
            return
        if random.random() >= contract_game.GRAIL_DROP_CHANCE:
            return
        self._drop_cd[gid] = now
        n = random.randint(contract_game.GRAIL_MIN, contract_game.GRAIL_MAX)
        try:
            await message.channel.send(
                "A **Holy Grail** shimmers into being... first to claim it wins.",
                view=GrailClaimView(self, n),
                delete_after=contract_game.CLAIM_TTL,
            )
        except discord.HTTPException:
            pass


class SummonView(discord.ui.View):
    """Ephemeral summon controls: Contract / Roll again (charges QP) / Dismiss. Only the
    summoner sees this (the message is ephemeral)."""

    def __init__(self, cog: ContractsCog, user_id: int, servant) -> None:
        super().__init__(timeout=SUMMON_VIEW_TIMEOUT)
        self.cog = cog
        self.user_id = user_id
        self.servant = servant
        self.interaction: discord.Interaction | None = None  # for greying out on timeout

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Contract", style=discord.ButtonStyle.success)
    async def contract(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        had = await self.cog.bot.contracts.active(interaction.guild_id, self.user_id)
        await self.cog.bot.contracts.contract(interaction.guild_id, self.user_id, self.servant.id)
        row = await self.cog.bot.contracts.active(interaction.guild_id, self.user_id)
        level = row["level"] if row else 1
        note = "Contract formed."
        if had is not None and had["servant_id"] != self.servant.id:
            prev = self.cog.bot.servants.get(had["servant_id"])
            note = (
                f"Contract formed. {prev.name if prev else 'Your previous servant'} was "
                "released (its progress is saved)."
            )
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.response.edit_message(
            embed=self.cog._servant_embed(
                self.servant, level, title=f"Contracted: {self.servant.name}", note=note
            ),
            view=self,
        )

    @discord.ui.button(label="Roll again", style=discord.ButtonStyle.secondary)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cost = self.cog.bot.config.contract_summon_cost
        bal = await self.cog.bot.scoring.get_balance(interaction.guild_id, self.user_id)
        if bal < cost:
            return await interaction.response.send_message(
                f"Not enough QP to roll again (need {qp(cost)}).", ephemeral=True
            )
        new_bal = await self.cog.bot.scoring.sub_qp(interaction.guild_id, self.user_id, cost)
        self.servant = contract_game.roll_servant(self.cog.bot.servants)
        is_new = not await self.cog.bot.contracts.has_contract(
            interaction.guild_id, self.user_id, self.servant.id
        )
        self.interaction = interaction  # freshest token, for greying out on timeout
        await interaction.response.edit_message(
            embed=self.cog._servant_embed(
                self.servant, 1, title=self.cog._summon_title(self.servant, is_new),
                qp_line=f"Spent {qp(cost)} · {qp(new_bal)} left",
            ),
            view=self,
        )

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.danger)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.response.edit_message(content="Summon dismissed.", embed=None, view=self)


class GrailClaimView(discord.ui.View):
    """A public grail drop: the first whitelisted user to click Claim takes 1-5 grails."""

    def __init__(self, cog: ContractsCog, n: int) -> None:
        super().__init__(timeout=contract_game.CLAIM_TTL)
        self.cog = cog
        self.n = n
        self.claimed = False

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, emoji="\N{SPARKLES}")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.cog._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if self.claimed:
            return await interaction.response.send_message("Already claimed.", ephemeral=True)
        self.claimed = True  # set before any await: view callbacks run serially, so first wins
        button.disabled = True
        self.stop()
        total = await self.cog.bot.contracts.grant_grails(
            interaction.guild_id, interaction.user.id, self.n
        )
        plural = "s" if self.n != 1 else ""
        await interaction.response.edit_message(
            content=f"{interaction.user.mention} claimed **{self.n}** grail{plural}! "
            f"(you now have {total})",
            view=self,
        )


async def setup(bot) -> None:
    await bot.add_cog(ContractsCog(bot))
