from __future__ import annotations

import logging
import pathlib
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from branding import qp
from data import contract_game
from data.grail_hosts import GRAIL_HOSTS
from data.servants import class_display
from permissions import is_mod

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

_GRAIL_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "grail"


def _grail_file(image: str) -> discord.File:
    """A fresh (single-use) File for a grail host's transparent portrait."""
    return discord.File(str(_GRAIL_DIR / image), filename="grail.png")

# Registered spawnable events for /triggerevent. Add a (value, label, spawner-method) row
# to extend it -- the command's choices AND its dispatch both derive from this list, and
# the passive on_message drops reuse the same spawner methods.
_EVENTS = [
    ("grail_single", "Single grail (Draco)", "_spawn_single"),
    ("grail_box", "Grail present box (Gilgamesh)", "_spawn_box"),
]
_EVENT_CHOICES = [app_commands.Choice(name=lbl, value=val) for val, lbl, _ in _EVENTS]
_EVENT_SPAWN = {val: method for val, _lbl, method in _EVENTS}
_EVENT_LABEL = {val: lbl for val, lbl, _ in _EVENTS}


def _stars(rarity: int) -> str:
    return f"{rarity}\N{BLACK STAR}"


class ContractsCog(commands.Cog):
    """The contracted-servant QP sink. Gated by config.contract_whitelist: bot.py only loads
    this cog when the whitelist is non-empty, and every entrypoint re-checks membership so
    only testers can use it (everyone else is silently ignored)."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._xp_cd: dict[tuple[int, int], float] = {}  # (guild,user) -> last xp monotonic
        self._single_cd: dict[int, float] = {}          # guild -> last single-grail monotonic
        self._box_cd: dict[int, float] = {}             # guild -> last grail-box monotonic

    def _allowed(self, user_id: int) -> bool:
        return user_id in self.bot.config.contract_whitelist

    @staticmethod
    def _summon_title(servant, is_new: bool) -> str:
        return f"Summoned: {servant.name}" + (" (NEW!)" if is_new else "")

    def _servant_embed(self, servant, level: int, *, title=None, note=None, qp_line=None, pity=None) -> discord.Embed:
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
        if pity is not None:
            embed.set_footer(
                text=f"Pity {pity}/{contract_game.PITY_5STAR} to a guaranteed 5\N{BLACK STAR}"
            )
        return embed

    async def _do_roll(self, guild_id: int, user_id: int):
        """Roll a servant with pity applied; returns (servant, pity_after) and persists the
        updated counter. Forces a 5-star when the streak would hit PITY_5STAR."""
        pity = await self.bot.contracts.pity_count(guild_id, user_id)
        wish = await self.bot.contracts.get_wish(guild_id, user_id)
        force = pity + 1 >= contract_game.PITY_5STAR
        servant = contract_game.roll_servant(self.bot.servants, force_5star=force, wish=wish)
        pity_after = 0 if contract_game.resets_pity(servant) else pity + 1
        await self.bot.contracts.set_pity(guild_id, user_id, pity_after)
        return servant, pity_after

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
        servant, pity_after = await self._do_roll(interaction.guild_id, interaction.user.id)
        is_new = not await self.bot.contracts.has_contract(
            interaction.guild_id, interaction.user.id, servant.id
        )
        view = SummonView(self, interaction.user.id, servant)
        view.interaction = interaction
        await interaction.response.send_message(
            embed=self._servant_embed(
                servant, 1, title=self._summon_title(servant, is_new),
                qp_line=f"{qp(cost)} \N{RIGHTWARDS ARROW} {qp(new_bal)}", pity=pity_after,
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
        wish_id = await self.bot.contracts.get_wish(interaction.guild_id, target.id)
        wished = self.bot.servants.get(wish_id) if wish_id else None
        if wished is not None:
            embed.add_field(
                name="Wishing for", value=f"{wished.name} ({_stars(wished.rarity)})", inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="items", description="See your QP and Holy Grails.")
    @app_commands.guild_only()
    async def items(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        bal = await self.bot.scoring.get_balance(interaction.guild_id, interaction.user.id)
        grails = await self.bot.contracts.grail_balance(interaction.guild_id, interaction.user.id)
        ge = self.bot.config.grail_emote
        embed = discord.Embed(title="Your Items", color=discord.Color.blurple())
        embed.add_field(name="QP", value=qp(bal))
        embed.add_field(name="Holy Grails", value=f"{grails:,} {ge}".strip() if ge else str(grails))
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

    @app_commands.command(name="wish", description="Chase a servant: it gets boosted summon odds (NPC bosses excluded).")
    @app_commands.guild_only()
    @app_commands.describe(servant="Servant to wish for (leave empty to clear your wish)")
    async def wish(self, interaction: discord.Interaction, servant: int | None = None) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if servant is None:
            await self.bot.contracts.set_wish(interaction.guild_id, interaction.user.id, None)
            return await interaction.response.send_message(
                "Wish cleared -- no servant is boosted.", ephemeral=True
            )
        s = self.bot.servants.get(servant)
        if s is None:
            return await interaction.response.send_message("No such servant.", ephemeral=True)
        if not contract_game.is_wishable(s):
            reason = "NPC bosses can't be wished." if s.npc else "That servant isn't summonable."
            return await interaction.response.send_message(reason, ephemeral=True)
        await self.bot.contracts.set_wish(interaction.guild_id, interaction.user.id, s.id)
        await interaction.response.send_message(
            f"Wish set: **{s.name}** ({_stars(s.rarity)}) now has boosted summon odds "
            "(~1% per roll) in your /summon.",
            ephemeral=True,
        )

    @wish.autocomplete("servant")
    async def _wish_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return [
            app_commands.Choice(name=f"{s.name[:80]} ({s.rarity}\N{BLACK STAR})", value=s.id)
            for s in self.bot.servants.search(current, 50)
            if contract_game.is_wishable(s)
        ][:25]

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

    @app_commands.command(name="triggerevent", description="(Mods) Spawn a server event now.")
    @app_commands.guild_only()
    @app_commands.describe(
        event="Which event (random if unset)",
        channel="Where to spawn it (this channel if unset)",
    )
    @app_commands.choices(event=_EVENT_CHOICES)
    async def triggerevent(
        self,
        interaction: discord.Interaction,
        event: app_commands.Choice[str] | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to spawn an event.", ephemeral=True
            )
        value = event.value if event else random.choice(list(_EVENT_SPAWN))
        target = channel or interaction.channel
        await getattr(self, _EVENT_SPAWN[value])(target)
        await interaction.response.send_message(
            f"Spawned **{_EVENT_LABEL[value]}** in {target.mention}.", ephemeral=True
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
        cg = contract_game
        if (now - self._single_cd.get(gid, 0.0) >= cg.GRAIL_SINGLE_COOLDOWN
                and random.random() < cg.GRAIL_SINGLE_CHANCE):
            self._single_cd[gid] = now
            await self._spawn_single(message.channel)
            return
        if (now - self._box_cd.get(gid, 0.0) >= cg.GRAIL_BOX_COOLDOWN
                and random.random() < cg.GRAIL_BOX_CHANCE):
            self._box_cd[gid] = now
            await self._spawn_box(message.channel)

    def _grail_title(self, text: str) -> str:
        ge = self.bot.config.grail_emote
        return f"{ge} {text}".strip() if ge else text

    async def _spawn_single(self, channel: discord.abc.Messageable) -> None:
        host = random.choice(list(GRAIL_HOSTS.values()))
        embed = discord.Embed(
            title=self._grail_title("A Holy Grail Appears!"),
            description=(
                f"**{host['name']}:** *\"{random.choice(host['single_appear'])}\"*\n\n"
                "A Holy Grail has manifested!\nBe the first to claim it!"
            ),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url="attachment://grail.png")
        view = SingleGrailView(self, host)
        try:
            view.message = await channel.send(
                embed=embed, file=_grail_file(host["image"]), view=view
            )
        except (discord.HTTPException, OSError):
            pass

    async def _spawn_box(self, channel: discord.abc.Messageable) -> None:
        host = random.choice(list(GRAIL_HOSTS.values()))
        uses = random.randint(contract_game.GRAIL_BOX_USES_MIN, contract_game.GRAIL_BOX_USES_MAX)
        view = BoxGrailView(self, host, uses)
        try:
            view.message = await channel.send(
                embed=view.render(), file=_grail_file(host["image"]), view=view
            )
        except (discord.HTTPException, OSError):
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
        self.servant, pity_after = await self.cog._do_roll(interaction.guild_id, self.user_id)
        is_new = not await self.cog.bot.contracts.has_contract(
            interaction.guild_id, self.user_id, self.servant.id
        )
        self.interaction = interaction  # freshest token, for greying out on timeout
        await interaction.response.edit_message(
            embed=self.cog._servant_embed(
                self.servant, 1, title=self.cog._summon_title(self.servant, is_new),
                qp_line=f"{qp(cost)} \N{RIGHTWARDS ARROW} {qp(new_bal)}", pity=pity_after,
            ),
            view=self,
        )

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.danger)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.response.edit_message(content="Summon dismissed.", embed=None, view=self)


class SingleGrailView(discord.ui.View):
    """A single grail (random host). The first whitelisted user claims exactly one, then it
    self-deletes; an unclaimed one self-deletes on timeout."""

    def __init__(self, cog: ContractsCog, host: dict) -> None:
        super().__init__(timeout=contract_game.GRAIL_EVENT_TTL)
        self.cog = cog
        self.host = host
        self.claimed = False
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if not self.claimed and self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Claim Holy Grail", style=discord.ButtonStyle.primary, emoji="\N{SPARKLES}")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.cog._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if self.claimed:
            return await interaction.response.send_message("Someone already claimed it.", ephemeral=True)
        self.claimed = True  # set before any await: callbacks run serially, so first wins
        button.disabled = True
        self.stop()
        total = await self.cog.bot.contracts.grant_grails(interaction.guild_id, interaction.user.id, 1)
        line = random.choice(self.host["single_claim"]).format(user=interaction.user.display_name)
        embed = discord.Embed(
            title=self.cog._grail_title("Holy Grail Claimed!"),
            description=(
                f"**{self.host['name']}:** *\"{line}\"*\n\n"
                f"{interaction.user.mention} now has **{total}** Holy Grail{'s' if total != 1 else ''}!"
            ),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url="attachment://grail.png")
        await interaction.response.edit_message(embed=embed, view=self)
        try:
            await interaction.message.delete(delay=6)
        except discord.HTTPException:
            pass


class BoxGrailView(discord.ui.View):
    """A grail present box (random host). Whitelisted users each take one grail until the
    box is empty, then it self-deletes; leftovers self-delete on timeout."""

    def __init__(self, cog: ContractsCog, host: dict, uses: int) -> None:
        super().__init__(timeout=contract_game.GRAIL_EVENT_TTL)
        self.cog = cog
        self.host = host
        self.uses = uses
        self.remaining = uses
        self.claimers: list[int] = []
        self.appear = random.choice(host["box_appear"])
        self.message: discord.Message | None = None

    def render(self, *, empty: bool = False) -> discord.Embed:
        if empty:
            line = random.choice(self.host["box_claim"]).format(user="everyone")
            body = f"**{self.host['name']}:** *\"{line}\"*\n\nThe box is empty."
        else:
            body = (
                f"**{self.host['name']}:** *\"{self.appear}\"*\n\n"
                "A treasure box has manifested! Each opening reveals a Holy Grail.\n\n"
                f"**{self.remaining}** of **{self.uses}** grails left."
            )
        embed = discord.Embed(
            title=self.cog._grail_title("A Grail Present Box Appears!"),
            description=body,
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url="attachment://grail.png")
        if self.claimers:
            embed.add_field(
                name="Claimed by",
                value=" ".join(f"<@{uid}>" for uid in self.claimers)[:1024],
                inline=False,
            )
        return embed

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Open the box", style=discord.ButtonStyle.primary, emoji="\N{SPARKLES}")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.cog._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if interaction.user.id in self.claimers:
            return await interaction.response.send_message("You already took one.", ephemeral=True)
        if self.remaining <= 0:
            return await interaction.response.send_message("The box is empty.", ephemeral=True)
        self.remaining -= 1
        self.claimers.append(interaction.user.id)
        await self.cog.bot.contracts.grant_grails(interaction.guild_id, interaction.user.id, 1)
        if self.remaining <= 0:
            for child in self.children:
                child.disabled = True
            self.stop()
            await interaction.response.edit_message(embed=self.render(empty=True), view=self)
            try:
                await interaction.message.delete(delay=6)
            except discord.HTTPException:
                pass
        else:
            await interaction.response.edit_message(embed=self.render(), view=self)


async def setup(bot) -> None:
    await bot.add_cog(ContractsCog(bot))
