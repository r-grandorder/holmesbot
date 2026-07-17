from __future__ import annotations

import datetime
import io
import logging
import pathlib
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from branding import qp
from data import contract_game
from data import images
from data.grail_hosts import GRAIL_HOSTS
from data.servants import class_display
from data.stimmy_hosts import STIMMY_HOSTS
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


_STIMMY_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "stimmy"


def _stimmy_file(image: str) -> discord.File:
    """A fresh (single-use) File for a QP-reward host's transparent portrait."""
    return discord.File(str(_STIMMY_DIR / image), filename="stimmy.png")

# Registered spawnable events for /triggerevent. Add a (value, label, spawner-method) row
# to extend it -- the command's choices AND its dispatch both derive from this list, and
# the passive on_message drops reuse the same spawner methods.
_EVENTS = [
    ("qp_reward", "QP reward (a chatter finds QP)"),
    ("grail_single", "Single grail (Draco)"),
    ("grail_box", "Grail present box (Gilgamesh)"),
]
_EVENT_CHOICES = [app_commands.Choice(name=lbl, value=val) for val, lbl in _EVENTS]
_EVENT_LABEL = {val: lbl for val, lbl in _EVENTS}
# Claim-style spawns take just a channel; qp_reward is an auto-award, dispatched separately.
_EVENT_SPAWN = {"grail_single": "_spawn_single", "grail_box": "_spawn_box"}


def _stars(rarity: int) -> str:
    return f"{rarity}\N{BLACK STAR}"


def _flavor(line: str | None, limit: int = 140) -> str | None:
    """A summon voice line trimmed for a short flavor blurb: whitespace collapsed, then cut at
    the last sentence break within `limit` (a clean full sentence), falling back to a word
    boundary + ellipsis. Some firstGet lines are paragraphs; the raw capture hard-cuts mid-word."""
    if not line:
        return line
    line = " ".join(line.split())
    if len(line) <= limit:
        return line
    head = line[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut >= 40:  # end on a full sentence when there's a clean one in range
        return head[: cut + 1]
    return head.rsplit(" ", 1)[0].rstrip(" .,;:!?-") + "\N{HORIZONTAL ELLIPSIS}"


def _progress_bar(have: int, need: int, length: int = 10) -> str:
    """A text progress bar in Bunyan's autobattle style, e.g. [####......] 40%."""
    pct = 0 if need <= 0 else max(0, min(100, round(100 * have / need)))
    filled = round(length * pct / 100)
    return f"[{'█' * filled}{'░' * (length - filled)}] {pct}%"


_ROSTER_PAGE = 8  # contracted servants shown per /servants page


def _acquired_ts(created_at: "str | None") -> "int | None":
    """SQLite CURRENT_TIMESTAMP text (UTC) -> unix seconds, for a Discord <t:...> date."""
    if not created_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.datetime.strptime(created_at, fmt)
            return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def _daily_reset_ts() -> int:
    """Unix time of the next UTC midnight -- when SQLite date('now') rolls over (the duel cap)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(nxt.timestamp())


def _war_bar(score: int, leader: int, length: int = 10) -> str:
    """A leader-relative bar (no percentage): the leader is full, the rest proportional."""
    filled = 0 if leader <= 0 else max(0, min(length, round(length * score / leader)))
    return f"[{'█' * filled}{'░' * (length - filled)}]"


class ContractsCog(commands.Cog):
    """The contracted-servant QP sink. Gated by config.contract_whitelist: bot.py only loads
    this cog when the whitelist is non-empty, and every entrypoint re-checks membership so
    only testers can use it (everyone else is silently ignored)."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._xp_cd: dict[tuple[int, int], float] = {}  # (guild,user) -> last xp monotonic
        self._single_cd: dict[int, float] = {}          # guild -> last single-grail monotonic
        self._box_cd: dict[int, float] = {}             # guild -> last grail-box monotonic
        self._qp_cd: dict[int, float] = {}              # guild -> last QP-reward monotonic
        self._duel_cd: dict[tuple[int, int], float] = {}  # (guild,challenger) -> last duel
        self._duel_pair_cd: dict = {}                     # (guild, frozenset{a,b}) -> last duel
        self._switch_cd: dict[tuple[int, int], float] = {}  # (guild,user) -> last active-swap

    async def cog_load(self) -> None:
        self._war_ticker.start()

    async def cog_unload(self) -> None:
        self._war_ticker.cancel()

    @tasks.loop(minutes=5)
    async def _war_ticker(self) -> None:
        """Auto-end wars past their scheduled end time, announcing in their start channel."""
        for row in await self.bot.wars.expired():
            text = await self._end_war(row["guild_id"])
            channel = self.bot.get_channel(row["channel_id"]) if row["channel_id"] else None
            if text and channel is not None:
                try:
                    await channel.send(text)
                except discord.HTTPException:
                    pass

    @_war_ticker.before_loop
    async def _war_ticker_before(self) -> None:
        await self.bot.wait_until_ready()

    def _allowed(self, user_id: int) -> bool:
        return self.bot.config.contract_open or user_id in self.bot.config.contract_whitelist

    async def _resolve_public(
        self, interaction: discord.Interaction, public: bool
    ) -> "tuple[bool, str | None]":
        """Mod-gate a public-post request on an otherwise-ephemeral command. Returns
        (post_public, note); `note` is a soft heads-up shown to a non-mod who asked for public
        (they still get the private view)."""
        if not public:
            return False, None
        if is_mod(interaction.user) or await self.bot.is_owner(interaction.user):
            return True, None
        return False, "Public posting is mods-only -- showing you privately."

    async def _notify_grant(
        self, interaction: discord.Interaction, target: discord.Member, text: str, quiet: bool
    ) -> None:
        """Ping the recipient of a mod grant with a short self-deleting notice -- unless it's a
        self-grant or the mod chose quiet. The mention pings so they actually see it; the
        message removes itself after 10s to avoid channel clutter."""
        if quiet or target.id == interaction.user.id or interaction.channel is None:
            return
        try:
            await interaction.channel.send(
                f"{target.mention} {text}",
                delete_after=10,
                allowed_mentions=discord.AllowedMentions(users=[target]),
            )
        except discord.HTTPException:
            pass

    @staticmethod
    def _summon_title(servant, is_new: bool) -> str:
        return f"Summoned: {servant.name}" + (" (NEW!)" if is_new else "")

    def _servant_embed(self, servant, level: int, *, title=None, note=None, qp_line=None, pity=None, allow=None, show_line: bool = True, spark: "str | None" = None) -> discord.Embed:
        embed = discord.Embed(
            title=title or servant.name,
            description=note,
            color=_RARITY_COLOR.get(servant.rarity, discord.Color.blurple()),
        )
        art = contract_game.display_art(servant, allow)
        # No safe art (fully restricted) -> show neither the figure nor the face portrait.
        if art:
            if servant.face:
                embed.set_thumbnail(url=servant.face)
            embed.set_image(url=art)
        embed.add_field(name="Class", value=class_display(servant.class_name) or "?")
        embed.add_field(name="Rarity", value=_stars(servant.rarity))
        embed.add_field(name="Power", value=f"{contract_game.power(servant, level):,}")
        line = _flavor(getattr(servant, "summon_line", None)) if show_line else None  # summon-only
        if line:
            embed.add_field(name="​", value=f"*{line}*", inline=False)
        if qp_line:
            embed.add_field(name="QP", value=qp_line, inline=False)
        if pity is not None:
            target = spark if spark else "5\N{BLACK STAR}"
            embed.set_footer(
                text=f"Guaranteed summon: {pity}/{contract_game.PITY_5STAR} ({target})"
            )
        return embed

    async def _do_roll(self, guild_id: int, user_id: int, allow=None):
        """Roll a servant with pity applied; returns (servant, pity_after) and persists the
        updated counter. Forces a 5-star when the streak would hit PITY_5STAR. Pity counts
        rolls toward that guarantee and carries over natural 5-stars -- only the guarantee
        resets it. `allow` is the content-policy gate (restricted servants excluded)."""
        pity = await self.bot.contracts.pity_count(guild_id, user_id)
        wish = await self.bot.contracts.get_wish(guild_id, user_id)
        force = pity + 1 >= contract_game.PITY_5STAR
        servant = contract_game.roll_servant(
            self.bot.servants, force_5star=force, wish=wish, allow=allow
        )
        pity_after = 0 if force else pity + 1  # carry pity over natural 5-stars; only the guarantee resets it
        await self.bot.contracts.set_pity(guild_id, user_id, pity_after)
        return servant, pity_after

    async def _spark_name(self, guild_id: int, user_id: int) -> "str | None":
        """Display name of the user's pity spark (their /wish), or None if unset/invalid."""
        wid = await self.bot.contracts.get_wish(guild_id, user_id)
        s = self.bot.servants.get(wid) if wid else None
        return s.name if (s and contract_game.is_wishable(s)) else None

    async def _announce_channel(self, guild_id: int):
        """The configured contract-announcement channel, or None to post in-context."""
        cid = await self.bot.guild_config.announce_channel(guild_id)
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        return ch if isinstance(ch, (discord.TextChannel, discord.Thread)) else None

    async def _broadcast(
        self, interaction: discord.Interaction, servant, *, title: str, action: str, allow=None,
        actor: "discord.abc.User | None" = None,
    ) -> None:
        """Post a public contract announcement to the announce channel, falling back to the
        channel the command happened in: a compact face thumbnail + the servant's (trimmed)
        summon voice line, respecting the art restriction gate. `actor` overrides who the
        announcement credits (defaults to the invoker); /grantservant passes the recipient so
        a granted contract reads like a normal one."""
        channel = await self._announce_channel(interaction.guild_id) or interaction.channel
        if channel is None:
            return
        who = actor or interaction.user
        desc = (
            f"{who.mention} {action} **{servant.name}** "
            f"({_stars(servant.rarity)})!"
        )
        line = _flavor(getattr(servant, "summon_line", None))
        if line:
            desc += f'\n\n*"{line}"*'
        embed = discord.Embed(
            title=title,
            description=desc,
            color=_RARITY_COLOR.get(servant.rarity, discord.Color.blurple()),
        )
        art = contract_game.display_art(servant, allow)
        if art and servant.face:  # gate the face on safe art (fully restricted -> no portrait)
            embed.set_thumbnail(url=servant.face)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

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
        allow = await self.bot.restrictions.build_allow()
        servant, pity_after = await self._do_roll(interaction.guild_id, interaction.user.id, allow)
        is_new = not await self.bot.contracts.has_contract(
            interaction.guild_id, interaction.user.id, servant.id
        )
        view = SummonView(self, interaction.user.id, servant)
        view.interaction = interaction
        spark = await self._spark_name(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            embed=self._servant_embed(
                servant, 1, title=self._summon_title(servant, is_new),
                qp_line=f"{qp(new_bal + cost)} \N{RIGHTWARDS ARROW} {qp(new_bal)}", pity=pity_after,
                allow=allow, spark=spark,
            ),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="redeem", description="Redeem a Summon Ticket: a boosted shot at your wish, a rare NPC/custom unit, or a 5-star.")
    @app_commands.guild_only()
    async def redeem(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if not await self.bot.contracts.use_ticket(interaction.guild_id, interaction.user.id):
            return await interaction.response.send_message(
                "You have no Summon Tickets. Win a faction war to earn them.", ephemeral=True
            )
        allow = await self.bot.restrictions.build_allow()
        wish = await self.bot.contracts.get_wish(interaction.guild_id, interaction.user.id)
        servant, is_wish = contract_game.ticket_roll(
            self.bot.servants, wish,
            chance=self.bot.config.summon_ticket_wish_chance, allow=allow,
        )
        is_new = not await self.bot.contracts.has_contract(
            interaction.guild_id, interaction.user.id, servant.id
        )
        if is_wish:
            note = "Your wished servant answers the call!"
        elif servant.npc or servant.custom:
            note = "A rare pull! The ticket turned up something special."
        else:
            note = "Not your wish this time, but a guaranteed 5-star."
        view = SummonView(self, interaction.user.id, servant, allow_reroll=False)
        view.interaction = interaction
        await interaction.response.send_message(
            embed=self._servant_embed(
                servant, 1,
                title=f"Summon Ticket: {servant.name}" + (" (NEW!)" if is_new else ""),
                note=note, allow=allow,
            ),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="profile", description="View your (or another player's) contracted servant.")
    @app_commands.guild_only()
    @app_commands.describe(
        member="Whose servant to view (defaults to you)",
        public="Mods only: post the card publicly instead of just to you",
    )
    async def profile(
        self, interaction: discord.Interaction, member: discord.Member | None = None,
        public: bool = False,
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
        allow = await self.bot.restrictions.build_allow()
        embed = self._servant_embed(
            servant, row["level"], title=f"{target.display_name}'s {servant.name}", allow=allow,
            show_line=False,
        )
        level = row["level"]
        embed.add_field(name="Level", value=f"{level} / {cap}")
        ge = self.bot.config.grail_emote
        embed.add_field(name="Grails", value=f"{grails:,} {ge}".strip() if ge else str(grails))
        if level < cap:
            need = contract_game.xp_to_next(level)
            embed.add_field(
                name="Progress",
                value=f"{_progress_bar(row['xp'], need)}\n{row['xp']:,} / {need:,} XP to Lv {level + 1}",
                inline=False,
            )
        else:
            embed.add_field(name="Progress", value="At cap -- use /grail to raise it", inline=False)
        wish_id = await self.bot.contracts.get_wish(interaction.guild_id, target.id)
        wished = self.bot.servants.get(wish_id) if wish_id else None
        if wished is not None:
            embed.add_field(
                name="Wishing for", value=f"{wished.name} ({_stars(wished.rarity)})", inline=False
            )
        is_public, note = await self._resolve_public(interaction, public)
        await interaction.response.send_message(
            content=note, embed=embed, ephemeral=not is_public,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="servants", description="Browse every servant you have contracted.")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose roster to view (defaults to you)")
    async def servants(
        self, interaction: discord.Interaction, member: "discord.Member | None" = None
    ) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        target = member or interaction.user
        rows = await self.bot.contracts.owned(interaction.guild_id, target.id)
        if not rows:
            own = target.id == interaction.user.id
            return await interaction.response.send_message(
                "You have not contracted any servants yet -- use /summon."
                if own
                else f"{target.display_name} has not contracted any servants.",
                ephemeral=True,
            )
        entries = []
        for r in rows:
            s = self.bot.servants.get(r["servant_id"])
            entries.append(
                {
                    "name": s.name if s else f"Servant #{r['servant_id']}",
                    "class": class_display(s.class_name) if s else "?",
                    "rarity": s.rarity if s else 0,
                    "level": r["level"],
                    "cap": contract_game.level_cap(r["grails_used"]),
                    "power": contract_game.power(s, r["level"]) if s else None,
                    "acquired": _acquired_ts(r["created_at"]),
                    "active": bool(r["active"]),
                }
            )
        view = RosterView(target, entries)
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)

    @app_commands.command(name="items", description="See your QP and Holy Grails.")
    @app_commands.guild_only()
    async def items(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        bal = await self.bot.scoring.get_balance(interaction.guild_id, interaction.user.id)
        grails = await self.bot.contracts.grail_balance(interaction.guild_id, interaction.user.id)
        tickets = await self.bot.contracts.summon_tickets(interaction.guild_id, interaction.user.id)
        ge = self.bot.config.grail_emote
        embed = discord.Embed(title="Your Items", color=discord.Color.blurple())
        embed.add_field(name="QP", value=qp(bal))
        te = self.bot.config.summon_ticket_emote
        embed.add_field(name="Holy Grails", value=f"{grails:,} {ge}".strip() if ge else str(grails))
        embed.add_field(name="Summon Tickets", value=f"{tickets:,} {te}".strip() if te else str(tickets))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shop", description="Visit Da Vinci's Workshop to spend QP on Holy Grails and Summon Tickets.")
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        view = ShopView(self, interaction.user.id)
        await interaction.response.send_message(
            embed=await view.render(interaction.guild_id), view=view, ephemeral=True
        )
        view.interaction = interaction

    @app_commands.command(name="grail", description="Spend a grail to raise a servant's cap (yours or another player's).")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose servant to grail (defaults to yours)")
    async def grail(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        target = member or interaction.user
        is_self = target.id == interaction.user.id
        status, cap, servant_id = await self.bot.contracts.apply_grail(
            interaction.guild_id, interaction.user.id, target.id
        )
        if status == "no_contract":
            msg = ("You have no active contract. Use /summon first." if is_self
                   else f"{target.display_name} has no active contract.")
            return await interaction.response.send_message(msg, ephemeral=True)
        if status == "no_grails":
            return await interaction.response.send_message(
                "You have no grails. Claim them from chat drops.", ephemeral=True
            )
        servant = self.bot.servants.get(servant_id) if servant_id else None
        if is_self:
            who = f"your **{servant.name}**" if servant else "your servant"
            return await interaction.response.send_message(
                f"Grail used -- {who}'s cap is now **{cap}**.", ephemeral=True
            )
        # grailing another player's servant: a public, celebratory embed with its portrait
        allow = await self.bot.restrictions.build_allow()
        whose = f"**{servant.name}**" if servant else "servant"
        embed = discord.Embed(
            title="Grail Bestowed",
            description=(
                f"{interaction.user.mention} grailed {target.mention}'s {whose} -- "
                f"level cap raised to **{cap}**!"
            ),
            color=(_RARITY_COLOR.get(servant.rarity, discord.Color.blurple())
                   if servant else discord.Color.blurple()),
        )
        if servant:
            art = contract_game.display_art(servant, allow)
            if art and servant.face:  # gate the portrait on safe art (fully restricted -> none)
                embed.set_thumbnail(url=servant.face)
        await interaction.response.send_message(
            embed=embed, allowed_mentions=discord.AllowedMentions(users=[target])
        )

    @app_commands.command(name="wish", description="Choose the servant your guaranteed summon delivers (NPC bosses excluded).")
    @app_commands.guild_only()
    @app_commands.describe(servant="Servant to spark (leave empty to clear)")
    async def wish(self, interaction: discord.Interaction, servant: int | None = None) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if servant is None:
            await self.bot.contracts.set_wish(interaction.guild_id, interaction.user.id, None)
            return await interaction.response.send_message(
                "Cleared -- your guaranteed summon is a random 5-star again.", ephemeral=True
            )
        s = self.bot.servants.get(servant)
        if s is None:
            return await interaction.response.send_message("No such servant.", ephemeral=True)
        if not contract_game.is_wishable(s):
            reason = "NPC bosses can't be wished." if s.npc else "That servant isn't summonable."
            return await interaction.response.send_message(reason, ephemeral=True)
        await self.bot.contracts.set_wish(interaction.guild_id, interaction.user.id, s.id)
        await interaction.response.send_message(
            f"Set: your guaranteed summon now delivers **{s.name}** ({_stars(s.rarity)}). "
            "This does not change your natural pull odds.",
            ephemeral=True,
        )

    @wish.autocomplete("servant")
    async def _wish_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return [
            app_commands.Choice(
                name=f"{s.name[:60]} ({class_display(s.class_name)}, {s.rarity}\N{BLACK STAR})",
                value=s.id,
            )
            for s in self.bot.servants.search(current, 50)
            if contract_game.is_wishable(s)
        ][:25]

    @app_commands.command(name="switch", description="Switch your active servant to one you have already contracted.")
    @app_commands.guild_only()
    @app_commands.describe(servant="A servant you already own (search by name)")
    async def switch(self, interaction: discord.Interaction, servant: int) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id
        if not await self.bot.contracts.has_contract(gid, uid, servant):
            return await interaction.response.send_message(
                "You have not contracted that servant. Use /summon to get a new one.", ephemeral=True
            )
        active = await self.bot.contracts.active(gid, uid)
        if active and active["servant_id"] == servant:
            return await interaction.response.send_message(
                "That servant is already active. Summon and contract another servant to have "
                "one to switch to.", ephemeral=True
            )
        now = time.monotonic()
        wait = contract_game.SWITCH_COOLDOWN - (now - self._switch_cd.get((gid, uid), 0.0))
        if wait > 0:
            return await interaction.response.send_message(
                f"You can switch again in {int(wait // 60) + 1} min.", ephemeral=True
            )
        cost = contract_game.SWITCH_COST
        bal = await self.bot.scoring.get_balance(gid, uid)
        if bal < cost:
            return await interaction.response.send_message(
                f"Switching costs {qp(cost)}; you have {qp(bal)}.", ephemeral=True
            )
        new_bal = await self.bot.scoring.sub_qp(gid, uid, cost)
        await self.bot.contracts.contract(gid, uid, servant)
        self._switch_cd[(gid, uid)] = now
        row = await self.bot.contracts.active(gid, uid)
        s = self.bot.servants.get(servant)
        name = s.name if s else "your servant"
        lvl = row["level"] if row else 1
        await interaction.response.send_message(
            f"Switched to **{name}** (Lv {lvl}). {qp(cost)} spent, balance now {qp(new_bal)}.",
            ephemeral=True,
        )

    @switch.autocomplete("servant")
    async def _switch_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        # Show the whole roster (active first, marked) rather than only switch targets: a player
        # who has contracted just one servant would otherwise see an empty box and think /switch
        # is broken. Picking the active one gets a helpful "already active" nudge.
        rows = await self.bot.contracts.owned(interaction.guild_id, interaction.user.id)
        q = current.strip().lower()
        out: list[app_commands.Choice[int]] = []
        for r in rows:
            s = self.bot.servants.get(r["servant_id"])
            if s is None or (q and q not in s.name.lower()):
                continue
            cap = contract_game.level_cap(r["grails_used"])
            tag = " (active)" if r["active"] else ""
            out.append(
                app_commands.Choice(name=f"{s.name[:60]} (Lv {r['level']}/{cap}){tag}", value=s.id)
            )
            if len(out) >= 25:
                break
        return out

    @app_commands.command(name="servantboard", description="Top contracted servants by level.")
    @app_commands.guild_only()
    @app_commands.describe(
        klass="Only show servants of this class",
        servant="Only show a specific servant (by name)",
        public="Mods only: post the leaderboard publicly instead of just to you",
    )
    @app_commands.rename(klass="class")
    @app_commands.choices(klass=filters.CLASS_CHOICES)
    async def servantboard(
        self,
        interaction: discord.Interaction,
        klass: app_commands.Choice[str] | None = None,
        servant: int | None = None,
        public: bool = False,
    ) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        rows = await self.bot.contracts.board(interaction.guild_id)
        suffix = ""
        empty = "No contracts yet."
        if servant is not None:  # a specific servant beats the class filter (more specific)
            rows = [r for r in rows if r["servant_id"] == servant]
            s = self.bot.servants.get(servant)
            label = s.name if s else f"#{servant}"
            suffix, empty = f" - {label}", f"Nobody has contracted {label} yet."
        elif klass is not None:
            rows = [
                r for r in rows
                if (s := self.bot.servants.get(r["servant_id"])) and s.class_name.lower() == klass.value
            ]
            suffix, empty = f" - {klass.name}", f"No {klass.name} contracts yet."
        # board() already returns one (active) row per user, level-sorted; take the top 10.
        seen: set[int] = set()
        top = []
        for r in rows:
            if r["user_id"] in seen:
                continue
            seen.add(r["user_id"])
            top.append(r)
            if len(top) >= 10:
                break
        rows = top
        if not rows:
            return await interaction.response.send_message(empty, ephemeral=True)
        lines = []
        for i, r in enumerate(rows, 1):
            s = self.bot.servants.get(r["servant_id"])
            name = s.name if s else f"#{r['servant_id']}"
            cap = contract_game.level_cap(r["grails_used"])
            lines.append(f"**{i}.** <@{r['user_id']}> - {name} (Lv {r['level']}/{cap})")
        is_public, note = await self._resolve_public(interaction, public)
        await interaction.response.send_message(
            content=note,
            embed=discord.Embed(title="Servant Leaderboard" + suffix, description="\n".join(lines)),
            ephemeral=not is_public,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @servantboard.autocomplete("servant")
    async def _servantboard_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return [
            app_commands.Choice(
                name=f"{s.name[:60]} ({class_display(s.class_name)}, {s.rarity}\N{BLACK STAR})",
                value=s.id,
            )
            for s in self.bot.servants.search(current, 25)
        ]

    @app_commands.command(name="duel", description="Duel another player's contracted servant (resolves instantly).")
    @app_commands.guild_only()
    @app_commands.describe(opponent="The player to challenge")
    async def duel(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if opponent.bot or opponent.id == interaction.user.id:
            return await interaction.response.send_message(
                "Pick another player to duel.", ephemeral=True
            )
        if not self._allowed(opponent.id):
            return await interaction.response.send_message(
                f"{opponent.display_name} isn't in the contract feature yet.", ephemeral=True
            )
        if await self.bot.contracts.active(interaction.guild_id, interaction.user.id) is None:
            return await interaction.response.send_message(
                "You have no active contract -- use /summon.", ephemeral=True
            )
        if await self.bot.contracts.active(interaction.guild_id, opponent.id) is None:
            return await interaction.response.send_message(
                f"{opponent.display_name} has no active contract.", ephemeral=True
            )
        gid = interaction.guild_id
        now = time.monotonic()
        pair = (gid, frozenset((interaction.user.id, opponent.id)))
        if now - self._duel_cd.get((gid, interaction.user.id), 0.0) < contract_game.DUEL_COOLDOWN:
            return await interaction.response.send_message(
                "You're dueling too fast -- give it a moment.", ephemeral=True
            )
        pair_wait = contract_game.DUEL_PAIR_COOLDOWN - (now - self._duel_pair_cd.get(pair, 0.0))
        if pair_wait > 0:
            return await interaction.response.send_message(
                f"You dueled {opponent.display_name} recently -- try again in {int(pair_wait)}s.",
                ephemeral=True,
            )
        self._duel_cd[(gid, interaction.user.id)] = now
        self._duel_pair_cd[pair] = now
        result = await self._duel_result(gid, interaction.user, opponent)
        if result is None:
            return await interaction.response.send_message(
                "A servant is no longer available.", ephemeral=True
            )
        embed, file = result
        await interaction.response.send_message(
            embed=embed,
            file=file if file is not None else discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _duel_result(self, gid: int, challenger, opponent):
        """Resolve a duel, award the daily-capped QP, and return the public result embed
        (or None if a servant vanished mid-resolve)."""
        a = await self.bot.contracts.active(gid, challenger.id)
        b = await self.bot.contracts.active(gid, opponent.id)
        sa = self.bot.servants.get(a["servant_id"]) if a else None
        sb = self.bot.servants.get(b["servant_id"]) if b else None
        if not (a and b and sa and sb):
            return None
        pa = contract_game.power(sa, a["level"])
        pb = contract_game.power(sb, b["level"])
        odds = contract_game.duel_odds(pa, sa.class_name, pb, sb.class_name)
        if random.random() < odds:
            winner, loser, ws, ls, wp, lp = challenger, opponent, sa, sb, pa, pb
        else:
            winner, loser, ws, ls, wp, lp = opponent, challenger, sb, sa, pb, pa
        cap = contract_game.DUEL_DAILY_CAP
        count = await self.bot.contracts.duel_reward_count(gid, winner.id)
        if count < cap:
            await self.bot.scoring.add_qp(gid, winner.id, contract_game.DUEL_REWARD)
            await self.bot.contracts.bump_duel_reward(gid, winner.id)
            wins_today = count + 1
            reward_line = f"\n\n{winner.display_name} earns {qp(contract_game.DUEL_REWARD)}."
        else:
            wins_today = count
            reward_line = f"\n\n{winner.display_name} won, but has no reward wins left today."
        desc = (
            f"{winner.mention}'s **{ws.name}** ({class_display(ws.class_name)}, Power {wp:,}) "
            f"defeated {loser.mention}'s **{ls.name}** "
            f"({class_display(ls.class_name)}, Power {lp:,})!"
        )
        if contract_game.class_multiplier(ws.class_name, ls.class_name) > 1:
            desc += f"\n{class_display(ws.class_name)} held the class advantage."
        desc += reward_line
        embed = discord.Embed(title="Duel Result", description=desc, color=discord.Color.green())
        cap_note = f"{winner.display_name}: {wins_today}/{cap} reward wins used today"
        if wins_today >= cap:
            cap_note += " (cap reached)"
        cap_note += f" \N{MIDDLE DOT} resets <t:{_daily_reset_ts()}:R>"
        embed.add_field(name="Daily reward cap", value=cap_note, inline=False)
        file = await self._duel_banner(ws, ls)
        if file is not None:
            embed.set_image(url="attachment://duel.png")
        return embed, file

    async def _duel_banner(self, winner_servant, loser_servant):
        """The winner-vs-loser face banner (winner on the left), or None on any hiccup."""
        session = self.bot.http_session
        if not (session and winner_servant.face and loser_servant.face):
            return None
        try:
            wb = await images.fetch_bytes(session, winner_servant.face)
            lb = await images.fetch_bytes(session, loser_servant.face)
            png = images.duel_banner(wb, lb)
        except Exception:  # cosmetic banner: any fetch/decode failure just drops it
            return None
        return discord.File(io.BytesIO(png), filename="duel.png")

    # ---- faction war ----
    @app_commands.command(name="warstart", description="(Mods) Start a faction war (2-4 factions).")
    @app_commands.guild_only()
    @app_commands.describe(
        faction_a="First faction name",
        faction_b="Second faction name",
        faction_c="Third faction (optional)",
        faction_d="Fourth faction (optional)",
        name="War title shown in /warstatus and announcements (optional)",
        description="Short flavor/rules blurb shown with the war (optional)",
        banner="Optional banner image shown on the war",
        days="Days until the war auto-ends (default 7)",
    )
    async def warstart(
        self,
        interaction: discord.Interaction,
        faction_a: str,
        faction_b: str,
        faction_c: str | None = None,
        faction_d: str | None = None,
        name: str | None = None,
        description: str | None = None,
        banner: discord.Attachment | None = None,
        days: float | None = None,
    ) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to start a war.", ephemeral=True
            )
        names = [n.strip()[:80] for n in (faction_a, faction_b, faction_c, faction_d) if n and n.strip()]
        if len(names) < 2:
            return await interaction.response.send_message(
                "A war needs at least 2 factions.", ephemeral=True
            )
        banner_bytes = None
        if banner is not None:
            if not (banner.content_type or "").startswith("image/"):
                return await interaction.response.send_message(
                    "The banner must be an image.", ephemeral=True
                )
            if banner.size > 8 * 1024 * 1024:
                return await interaction.response.send_message(
                    "Banner too large (max 8 MB).", ephemeral=True
                )
            banner_bytes = await banner.read()
        length = contract_game.WAR_DEFAULT_DAYS if days is None else max(0.01, days)
        ends_at = int(time.time() + length * 86400)
        clean_name = name.strip()[:80] if name and name.strip() else None
        clean_desc = description.strip()[:400] if description and description.strip() else None
        await self.bot.wars.start(
            interaction.guild_id, names, banner_bytes, ends_at, interaction.channel_id,
            name=clean_name, description=clean_desc,
        )
        embed = discord.Embed(
            title=clean_name or "The War Begins!",
            description=(
                (f"*{clean_desc}*\n\n" if clean_desc else "")
                + "Factions: " + ", ".join(f"**{n}**" for n in names)
                + ".\nPlayers, /warjoin a side -- every level you gain scores for your faction.\n"
                + f"Ends <t:{ends_at}:R>."
            ),
            color=discord.Color.orange(),
        )
        file = discord.utils.MISSING
        if banner_bytes:
            embed.set_image(url="attachment://banner.png")
            file = discord.File(io.BytesIO(banner_bytes), filename="banner.png")
        await interaction.response.send_message(embed=embed, file=file)

    @app_commands.command(name="warjoin", description="Join the faction war (pick a side, or leave blank to join the weakest by points).")
    @app_commands.guild_only()
    @app_commands.describe(faction="Which faction to join (blank = auto-placed on the weakest side by points)")
    async def warjoin(self, interaction: discord.Interaction, faction: str | None = None) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if not await self.bot.wars.active(interaction.guild_id):
            return await interaction.response.send_message("No war is running right now.", ephemeral=True)
        name, already = await self.bot.wars.join(interaction.guild_id, interaction.user.id, faction)
        if name is None:
            return await interaction.response.send_message(
                "No faction by that name -- check /warstatus for the sides.", ephemeral=True
            )
        if already:
            return await interaction.response.send_message(
                f"You're already fighting for **{name}** this season.", ephemeral=True
            )
        await interaction.response.send_message(
            f"You've joined **{name}**! Your level-ups now score for them.", ephemeral=True
        )

    @warjoin.autocomplete("faction")
    async def _warjoin_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        standings = await self.bot.wars.standings(interaction.guild_id)
        return [
            app_commands.Choice(name=f["name"], value=f["name"])
            for f in standings
            if current.lower() in f["name"].lower()
        ][:25]

    @app_commands.command(name="warstatus", description="Show the faction war standings.")
    @app_commands.guild_only()
    @app_commands.describe(public="Mods only: post the standings publicly instead of just to you")
    async def warstatus(self, interaction: discord.Interaction, public: bool = False) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        if not await self.bot.wars.active(interaction.guild_id):
            return await interaction.response.send_message("No war is running right now.", ephemeral=True)
        is_public, note = await self._resolve_public(interaction, public)
        standings = await self.bot.wars.standings(interaction.guild_id)
        leader = standings[0]["score"] if standings else 0
        avg_size = (sum(f["members"] for f in standings) / len(standings)) if standings else 0
        te = self.bot.config.summon_ticket_emote
        lines = []
        for i, f in enumerate(standings, 1):
            bar = _war_bar(f["score"], leader)
            factor = contract_game.underdog_factor(f["members"], avg_size)
            tk = contract_game.war_ticket_reward(factor)
            wq = round(contract_game.WAR_REWARD * factor)
            reward = f"win {tk} ticket{'s' if tk != 1 else ''}{' ' + te if te else ''} + {qp(wq)}"
            lines.append(
                f"**{i}.** {f['name']}  {bar}  {f['score']:,} pts \N{MIDDLE DOT} "
                f"{f['members']} members \N{MIDDLE DOT} {reward}"
            )
        if not is_public:  # the personal line is invoker-specific; omit it from a public post
            mine = await self.bot.wars.member(interaction.guild_id, interaction.user.id)
            if mine is not None:
                lines.append(f"\nYour faction: **{mine['name']}** -- you've scored {mine['score']:,} pts")
            else:
                lines.append("\nYou haven't joined -- use /warjoin.")
        ends = await self.bot.wars.ends_at(interaction.guild_id)
        if ends:
            lines.append(f"Ends <t:{ends}:R>")
        war_name = await self.bot.wars.name(interaction.guild_id)
        war_desc = await self.bot.wars.description(interaction.guild_id)
        body = "\n".join(lines)
        if war_desc:
            body = f"*{war_desc}*\n\n" + body
        embed = discord.Embed(title=war_name or "Faction War", description=body)
        banner = await self.bot.wars.banner(interaction.guild_id)
        file = discord.utils.MISSING
        if banner:
            embed.set_image(url="attachment://banner.png")
            file = discord.File(io.BytesIO(banner), filename="banner.png")
        await interaction.response.send_message(
            content=note, embed=embed, file=file, ephemeral=not is_public,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="warend", description="(Mods) End the faction war now and reward the winner.")
    @app_commands.guild_only()
    async def warend(self, interaction: discord.Interaction) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to end a war.", ephemeral=True
            )
        text = await self._end_war(interaction.guild_id)
        if text is None:
            return await interaction.response.send_message("No war is running.", ephemeral=True)
        await interaction.response.send_message(text)

    async def _end_war(self, guild_id: int) -> "str | None":
        """Resolve + close an active war, granting rewards. Returns the announcement text, or
        None if no war is active. Shared by /warend and the auto-end ticker."""
        if not await self.bot.wars.active(guild_id):
            return None
        war_name = await self.bot.wars.name(guild_id)
        cap_label = war_name or "The war"  # capitalized for sentence starts
        standings = await self.bot.wars.standings(guild_id)
        await self.bot.wars.end(guild_id)
        if not standings or standings[0]["score"] == 0:
            return f"{cap_label} ends with no points scored -- no winner."
        top = standings[0]["score"]
        winners = [f for f in standings if f["score"] == top]
        if len(winners) > 1:
            names = " and ".join(f"**{w['name']}**" for w in winners)
            return f"{cap_label} ends in a tie between {names} at {top:,} pts! No payout."
        win = winners[0]
        members = await self.bot.wars.faction_members(guild_id, win["slot"])
        avg_size = sum(f["members"] for f in standings) / len(standings)
        factor = contract_game.underdog_factor(win["members"], avg_size)
        tickets_each = contract_game.war_ticket_reward(factor)
        qp_each = round(contract_game.WAR_REWARD * factor)
        for uid in members:
            await self.bot.scoring.add_qp(guild_id, uid, qp_each)
            await self.bot.contracts.grant_tickets(guild_id, uid, tickets_each)
        te = self.bot.config.summon_ticket_emote
        ticket_word = f"{tickets_each} Summon Ticket{'s' if tickets_each != 1 else ''}"
        bonus = " (outnumbered-win bonus!)" if factor > 1.0 else ""
        return (
            f"**{win['name']}** wins {war_name or 'the war'} with **{top:,} pts**!{bonus} Each of the "
            f"{len(members)} member(s) earns **{ticket_word}**{' ' + te if te else ''} + {qp(qp_each)}."
        )

    @app_commands.command(name="setservantlevel", description="(Mods) Set a member's contracted servant level.")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose servant to adjust", level="The level to set")
    async def setservantlevel(
        self, interaction: discord.Interaction, member: discord.Member, level: int
    ) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to set servant levels.", ephemeral=True
            )
        row = await self.bot.contracts.active(interaction.guild_id, member.id)
        if row is None:
            return await interaction.response.send_message(
                f"{member.display_name} has no active contract.", ephemeral=True
            )
        cap = contract_game.level_cap(row["grails_used"])
        if not 1 <= level <= cap:
            return await interaction.response.send_message(
                f"Level must be between 1 and {cap} -- that's the current grail cap for "
                f"{member.display_name}'s servant. Grail it first to go higher.",
                ephemeral=True,
            )
        await self.bot.contracts.set_active_level(interaction.guild_id, member.id, level)
        servant = self.bot.servants.get(row["servant_id"])
        name = servant.name if servant else "their servant"
        await interaction.response.send_message(
            f"Set {member.display_name}'s **{name}** to level **{level}** (cap {cap}).",
            ephemeral=True,
        )

    @app_commands.command(name="grantservant", description="(Mods) Grant a servant contract to a member.")
    @app_commands.guild_only()
    @app_commands.describe(
        servant="The servant to grant (search by name)",
        member="Who receives it (defaults to you)",
        overwrite="Required to replace an existing active contract",
        quiet="Skip the recipient notification (silent grant)",
    )
    async def grantservant(
        self,
        interaction: discord.Interaction,
        servant: int,
        member: discord.Member | None = None,
        overwrite: bool = False,
        quiet: bool = False,
    ) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to grant servants.", ephemeral=True
            )
        s = self.bot.servants.get(servant)
        if s is None:
            return await interaction.response.send_message("No such servant.", ephemeral=True)
        target = member or interaction.user
        current = await self.bot.contracts.active(interaction.guild_id, target.id)
        if current is not None and current["servant_id"] != servant and not overwrite:
            cur = self.bot.servants.get(current["servant_id"])
            cur_name = cur.name if cur else f"ID {current['servant_id']}"
            return await interaction.response.send_message(
                f"{target.display_name} already has **{cur_name}** contracted. Re-run with "
                f"overwrite: True to switch to {s.name} (their {cur_name} progress is saved and "
                "resumes if re-contracted).",
                ephemeral=True,
            )
        await self.bot.contracts.contract(interaction.guild_id, target.id, servant)
        whose = "your" if target.id == interaction.user.id else f"{target.display_name}'s"
        await interaction.response.send_message(
            f"Granted **{s.name}** ({_stars(s.rarity)}) as {whose} active contract.",
            ephemeral=True,
        )
        # Announce it like a regular summon-contract, crediting the recipient (quiet skips it).
        if not quiet:
            allow = await self.bot.restrictions.build_allow()
            await self._broadcast(
                interaction, s, title="New Contract",
                action="formed a contract with", allow=allow, actor=target,
            )

    @grantservant.autocomplete("servant")
    async def _grantservant_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return [
            app_commands.Choice(name=f"{s.name[:90]} (#{s.id})", value=s.id)
            for s in self.bot.servants.search(current, 25)
        ]

    @app_commands.command(name="grantitems", description="(Mods) Set or add a member's grails and Summon Tickets.")
    @app_commands.guild_only()
    @app_commands.describe(
        member="Whose items to adjust (defaults to you)",
        grails="Grail amount (omit to leave unchanged)",
        tickets="Summon Ticket amount (omit to leave unchanged)",
        mode="add (default) adjusts by the amount; set replaces the balance",
        quiet="Skip the recipient notification (silent grant)",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="set", value="set"),
        ]
    )
    async def grantitems(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        grails: int | None = None,
        tickets: int | None = None,
        mode: app_commands.Choice[str] | None = None,
        quiet: bool = False,
    ) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to grant items.", ephemeral=True
            )
        if grails is None and tickets is None:
            return await interaction.response.send_message(
                "Specify grails and/or tickets to change.", ephemeral=True
            )
        setmode = (mode.value if mode else "add") == "set"
        if setmode and ((grails is not None and grails < 0) or (tickets is not None and tickets < 0)):
            return await interaction.response.send_message(
                "A set amount can't be negative.", ephemeral=True
            )
        target = member or interaction.user
        gid = interaction.guild_id
        parts: list[str] = []
        if grails is not None:
            if setmode:
                newg = await self.bot.contracts.set_grails(gid, target.id, grails)
            else:
                newg = await self.bot.contracts.grant_grails(gid, target.id, grails)
                if newg < 0:
                    newg = await self.bot.contracts.set_grails(gid, target.id, 0)
            parts.append(f"grails: **{newg}**")
        if tickets is not None:
            if setmode:
                newt = await self.bot.contracts.set_tickets(gid, target.id, tickets)
            else:
                newt = await self.bot.contracts.grant_tickets(gid, target.id, tickets)
                if newt < 0:
                    newt = await self.bot.contracts.set_tickets(gid, target.id, 0)
            parts.append(f"tickets: **{newt}**")
        whose = "your" if target.id == interaction.user.id else f"{target.display_name}'s"
        await interaction.response.send_message(
            f"Updated {whose} items -- " + ", ".join(parts) + ".", ephemeral=True
        )
        mod = interaction.user.display_name
        if setmode:
            sbits = []
            if grails is not None:
                sbits.append(f"Holy Grails to **{grails}**")
            if tickets is not None:
                sbits.append(f"Summon Tickets to **{tickets}**")
            notice = f"**{mod}** set your " + " and ".join(sbits) + "."
        else:
            gbits = []
            if grails is not None:
                gbits.append(f"**{grails}** Holy Grail{'s' if abs(grails) != 1 else ''}")
            if tickets is not None:
                gbits.append(f"**{tickets}** Summon Ticket{'s' if abs(tickets) != 1 else ''}")
            notice = f"**{mod}** granted you " + " and ".join(gbits) + "!"
        await self._notify_grant(interaction, target, notice, quiet)

    @app_commands.command(name="summonodds", description="(Mods) Show the live summon rate table (tune custom-unit weights).")
    @app_commands.guild_only()
    async def summonodds(self, interaction: discord.Interaction) -> None:
        if not (is_mod(interaction.user) or await self.bot.is_owner(interaction.user)):
            return await interaction.response.send_message(
                "You need moderator permissions to view summon odds.", ephemeral=True
            )
        allow = await self.bot.restrictions.build_allow()
        rows, _ = contract_game.summon_rates(self.bot.servants, allow=allow)
        tier_lines, custom_lines = [], []
        for kind, label, weight, pct, count, each in rows:
            if kind == "custom":
                custom_lines.append(f"**{label}**: {pct:.3f}%  (weight {weight:g})")
            elif count > 1:
                tier_lines.append(f"**{label}**: {pct:.2f}%  ({count} servants, ~{each:.3f}% each)")
            else:
                tier_lines.append(f"**{label}**: {pct:.2f}%")
        desc = "Chance per single summon.\n\n" + "\n".join(tier_lines)
        if custom_lines:
            desc += "\n\n__Custom units__\n" + "\n".join(custom_lines)
        else:
            desc += "\n\n_No custom units configured yet (add them in custom_servants.json)._"
        embed = discord.Embed(title="Summon Odds", description=desc, color=discord.Color.blurple())
        embed.set_footer(
            text="Excludes the per-player wish tier (~1%). summon_weight is roughly the percent chance."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        value = event.value if event else random.choice([v for v, _ in _EVENTS])
        target = channel or interaction.channel
        if value == "qp_reward":
            await self._award_qp(target, interaction.user)
        else:
            await getattr(self, _EVENT_SPAWN[value])(target)
        await interaction.response.send_message(
            f"Spawned **{_EVENT_LABEL[value]}** in {target.mention}.", ephemeral=True
        )

    # ---- passive: XP + event drops from chatting ----
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content:
            return
        if not self._allowed(message.author.id):
            return
        await self._grant_xp(message)
        await self._maybe_drop_event(message)

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
        # faction war: each level gained scores for the player's faction (no-op if no war)
        await self.bot.wars.add_points(message.guild.id, message.author.id, new_level - old_level)
        mode = self.bot.config.levelup_announce
        if mode == "off":
            return
        every = contract_game.LEVELUP_MILESTONE_EVERY
        hit_cap = new_level >= cap and old_level < cap
        crossed_milestone = new_level // every > old_level // every
        if mode == "milestones" and not (hit_cap or crossed_milestone):
            return  # regular level; watch progress on /profile
        servant = self.bot.servants.get(servant_id)
        name = servant.name if servant else "Your servant"
        tail = "  (at cap -- /grail to raise it)" if new_level >= cap else ""
        announce = await self._announce_channel(message.guild.id)
        target = announce or message.channel
        try:
            await target.send(
                f"{message.author.display_name}'s **{name}** reached level **{new_level}**!{tail}",
                delete_after=None if announce else 12,  # persist in the feed; self-clean in-context
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    async def _maybe_drop_event(self, message: discord.Message) -> None:
        if not await self.bot.guild_config.is_event_channel_allowed(
            message.guild.id, message.channel.id
        ):
            return
        gid = message.guild.id
        now = time.monotonic()
        cg = contract_game
        if (now - self._qp_cd.get(gid, 0.0) >= cg.QP_REWARD_COOLDOWN
                and random.random() < cg.QP_REWARD_CHANCE):
            self._qp_cd[gid] = now
            await self._award_qp(message.channel, message.author)
            return
        if (now - self._single_cd.get(gid, 0.0) >= cg.GRAIL_SINGLE_COOLDOWN
                and random.random() < cg.GRAIL_SINGLE_CHANCE):
            self._single_cd[gid] = now
            await self._spawn_single(message.channel)
            return
        if (now - self._box_cd.get(gid, 0.0) >= cg.GRAIL_BOX_COOLDOWN
                and random.random() < cg.GRAIL_BOX_CHANCE):
            self._box_cd[gid] = now
            await self._spawn_box(message.channel)

    async def _award_qp(self, channel: discord.abc.Messageable, user) -> None:
        """Bunyan-style QP reward: auto-award `user` a random (exponential) QP amount with a
        random wealth-servant host, in a self-deleting notification."""
        host = random.choice(list(STIMMY_HOSTS.values()))
        amount = contract_game.qp_reward_amount(host["qp"])
        new_bal = await self.bot.scoring.add_qp(channel.guild.id, user.id, amount)
        embed = discord.Embed(
            title="Random Encounter!",
            description=(
                f"**{host['name']}:** *\"{random.choice(host['lines'])}\"*\n\n"
                f"**{user.display_name}** found {qp(amount)}!\nBalance: {qp(new_bal)}"
            ),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url="attachment://stimmy.png")
        try:
            await channel.send(
                embed=embed, file=_stimmy_file(host["image"]),
                delete_after=contract_game.QP_REWARD_TTL,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, OSError):
            pass

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


_SHOP_HOST_ID = 403500  # Rider Da Vinci -- FGO's shopkeeper, hosts /shop
_SHOP_HOST_LINES = [
    "Welcome to my workshop! Now, what can I interest a master like you in?",
    "A paying customer! Genius craftsmanship at reasonable-ish prices, just for you, Master.",
    "QP burning a hole in your pocket, Master? Allow me to help with that.",
    "Everything here is handcrafted by yours truly, a universal genius. What'll it be?",
]


class RosterView(discord.ui.View):
    """Ephemeral, paginated /servants roster. Entries are pre-resolved dicts (name/class/rarity/
    level/cap/power/acquired/active), so the view is pure display. Ephemeral -> only the invoker
    sees or drives it, so no invoker guard (same as SummonView/ShopView)."""

    def __init__(self, target: "discord.abc.User", entries: list) -> None:
        super().__init__(timeout=180)
        self.target = target
        self.entries = entries
        self.page = 0
        self.pages = max(1, (len(entries) + _ROSTER_PAGE - 1) // _ROSTER_PAGE)
        self._sync()

    def _sync(self) -> None:
        self.go_prev.disabled = self.page <= 0
        self.go_next.disabled = self.page >= self.pages - 1

    def render(self) -> discord.Embed:
        start = self.page * _ROSTER_PAGE
        blocks = []
        for e in self.entries[start : start + _ROSTER_PAGE]:
            power = f"Power {e['power']:,}" if e["power"] is not None else "Power ?"
            acq = f" · contracted <t:{e['acquired']}:D>" if e["acquired"] else ""
            active = "  **(active)**" if e["active"] else ""
            blocks.append(
                f"**{e['name']}**{active}\n"
                f"{e['class']} · {_stars(e['rarity'])} · Lv {e['level']}/{e['cap']} · {power}{acq}"
            )
        embed = discord.Embed(
            title=f"{self.target.display_name}'s Servants ({len(self.entries)})",
            description="\n\n".join(blocks),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=self.target.display_avatar.url)
        embed.set_footer(text=f"Page {self.page + 1}/{self.pages}")
        return embed

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def go_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def go_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.render(), view=self)


class ShopView(discord.ui.View):
    """Ephemeral QP shop (/shop): buy Holy Grails and Summon Tickets. Invoker-scoped; the embed
    refreshes after each purchase so the running QP balance stays accurate. Purchases draw from
    the spendable balance only, so they never touch the (lifetime) QP leaderboard."""

    def __init__(self, cog, user_id: int) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.interaction: "discord.Interaction | None" = None
        self.line = random.choice(_SHOP_HOST_LINES)  # host greeting, stable for this session
        cfg = cog.bot.config
        grail_btn = discord.ui.Button(
            label=f"Grail ({cfg.shop_grail_cost:,} QP)", style=discord.ButtonStyle.primary
        )
        grail_btn.callback = self._buy_grail
        self.add_item(grail_btn)
        ticket_btn = discord.ui.Button(
            label=f"Summon Ticket ({cfg.shop_ticket_cost:,} QP)", style=discord.ButtonStyle.success
        )
        ticket_btn.callback = self._buy_ticket
        self.add_item(ticket_btn)

    async def render(self, guild_id: int) -> discord.Embed:
        cfg = self.cog.bot.config
        bal = await self.cog.bot.scoring.get_balance(guild_id, self.user_id)
        grails = await self.cog.bot.contracts.grail_balance(guild_id, self.user_id)
        tickets = await self.cog.bot.contracts.summon_tickets(guild_id, self.user_id)
        ge, te = cfg.grail_emote, cfg.summon_ticket_emote
        host = self.cog.bot.servants.get(_SHOP_HOST_ID)
        embed = discord.Embed(
            title="QP Shop",
            description=(
                f'*"{self.line}"*\n\n'
                f"Your QP: **{qp(bal)}**\n\n"
                f"**Holy Grail** ({qp(cfg.shop_grail_cost)}) -- goes to your stash; spend it with /grail to raise a cap by 5.\n"
                f"**Summon Ticket** ({qp(cfg.shop_ticket_cost)}) -- redeem with /redeem for a boosted pull."
            ),
            color=discord.Color.blurple(),
        )
        if host is not None and host.face:
            embed.set_author(name=f"{host.name}'s Workshop", icon_url=host.face)
        embed.add_field(
            name="Your Holy Grails", value=f"{grails:,} {ge}".strip() if ge else str(grails)
        )
        embed.add_field(
            name="Your Summon Tickets", value=f"{tickets:,} {te}".strip() if te else str(tickets)
        )
        return embed

    async def _buy(self, interaction: discord.Interaction, cost: int, grant, label: str) -> None:
        # The shop message is ephemeral, so only its invoker can see or click it -- no
        # invoker-id guard needed (same as SummonView).
        gid = interaction.guild_id
        if not await self.cog.bot.scoring.try_spend(gid, self.user_id, cost):
            bal = await self.cog.bot.scoring.get_balance(gid, self.user_id)
            return await interaction.response.send_message(
                f"Not enough QP for a {label}: it costs {qp(cost)}, you have {qp(bal)}.", ephemeral=True
            )
        await grant(gid, self.user_id, 1)
        await interaction.response.edit_message(embed=await self.render(gid), view=self)

    async def _buy_grail(self, interaction: discord.Interaction) -> None:
        await self._buy(
            interaction, self.cog.bot.config.shop_grail_cost,
            self.cog.bot.contracts.grant_grails, "Holy Grail",
        )

    async def _buy_ticket(self, interaction: discord.Interaction) -> None:
        await self._buy(
            interaction, self.cog.bot.config.shop_ticket_cost,
            self.cog.bot.contracts.grant_tickets, "Summon Ticket",
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass


class SummonView(discord.ui.View):
    """Ephemeral summon controls: Contract / Roll again (charges QP) / Dismiss. Only the
    summoner sees this (the message is ephemeral)."""

    def __init__(self, cog: ContractsCog, user_id: int, servant, *, allow_reroll: bool = True) -> None:
        super().__init__(timeout=SUMMON_VIEW_TIMEOUT)
        self.cog = cog
        self.user_id = user_id
        self.servant = servant
        self.interaction: discord.Interaction | None = None  # for greying out on timeout
        if not allow_reroll:  # ticket pulls don't re-roll for QP
            for child in list(self.children):
                if getattr(child, "label", None) == "Roll again":
                    self.remove_item(child)

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
                "released (their progress is saved)."
            )
        for child in self.children:
            child.disabled = True
        self.stop()
        allow = await self.cog.bot.restrictions.build_allow()
        await interaction.response.edit_message(
            embed=self.cog._servant_embed(
                self.servant, level, title=f"Contracted: {self.servant.name}", note=note,
                allow=allow,
            ),
            view=self,
        )
        await self.cog._broadcast(
            interaction, self.servant, title="New Contract",
            action="formed a contract with", allow=allow,
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
        allow = await self.cog.bot.restrictions.build_allow()
        self.servant, pity_after = await self.cog._do_roll(interaction.guild_id, self.user_id, allow)
        is_new = not await self.cog.bot.contracts.has_contract(
            interaction.guild_id, self.user_id, self.servant.id
        )
        self.interaction = interaction  # freshest token, for greying out on timeout
        spark = await self.cog._spark_name(interaction.guild_id, self.user_id)
        await interaction.response.edit_message(
            embed=self.cog._servant_embed(
                self.servant, 1, title=self.cog._summon_title(self.servant, is_new),
                qp_line=f"{qp(new_bal + cost)} \N{RIGHTWARDS ARROW} {qp(new_bal)}", pity=pity_after,
                allow=allow, spark=spark,
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
