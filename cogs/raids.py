"""Co-op raids cog (loaded with autobattle, so it ships dark).

A server-wide boss with a shared HP pool that everyone chips down with their /ab team. Each fight is a
winnable per-battle version of the boss (its `battle_hp`); the damage you deal to that micro-fight is
subtracted from the shared pool. As the pool drops the boss enters phases that swap its kit/name/flavor/
sprite. On defeat/expiry, rewards pay out by contribution (participation / rank / last-hit).

  /raid          -- fight the active raid boss with your team
  /raidstatus    -- live HP / phase / time left / damage leaderboard
  /raidhistory   -- browse past raids + their final leaderboards
  /raidstart <def> (mods)  -- start an enabled raid definition here
  /raidend         (mods)  -- force-end the active raid

Team-building, the composite battle image, and the status blocks are reused from the Autobattle cog
(both are loaded together) via get_cog. Definitions/instances are configured from the website.
"""
from __future__ import annotations

import datetime as dt
import io
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from branding import qp
from data import autobattle
from data import autobattle_engine as engine
from data import images
from data import raids as R
from data.kits import Skill
from permissions import is_mod
from cogs.autobattle import LogPager, _DENY

_UTC = dt.timezone.utc


def _parse_ts(s: "str | None") -> "dt.datetime | None":
    if not s:
        return None
    try:
        return dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC)
    except ValueError:
        return None


def _time_left(expires_at: "str | None") -> str:
    end = _parse_ts(expires_at)
    if end is None:
        return "unknown"
    secs = int((end - dt.datetime.now(_UTC)).total_seconds())
    if secs <= 0:
        return "ending soon"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


class _CustomBoss:
    """A servant-less raid boss: just enough of the Servant shape for the engine + sprite lookups,
    so a raid boss need not be tied to a real servant (uses configured ATK/class + an uploaded sprite)."""

    def __init__(self, name: str, class_name: str, atk: int, hp: int) -> None:
        self.id = 0
        self.name = name or "Boss"
        self.class_name = class_name or ""
        self.rarity = 5
        self.atk_base = self.atk_max = max(1, int(atk))
        self.hp_base = self.hp_max = max(1, int(hp))
        self.art: dict = {}
        self.figure: dict = {}
        self.commands: dict = {}
        self.face = None


class Raids(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._cd: dict[tuple[int, int], float] = {}   # (guild,user) -> last raid-fight monotonic
        self._expiry.start()

    def cog_unload(self) -> None:
        self._expiry.cancel()

    # ---- helpers ----
    def _ab(self):
        return self.bot.get_cog("Autobattle")  # sibling cog; always loaded alongside raids

    def _allowed(self, uid: int) -> bool:
        return self.bot.config.contract_open or uid in self.bot.config.contract_whitelist

    def _is_mod(self, user) -> bool:
        return user.id in self.bot.config.dashboard_mod_ids or is_mod(user)

    def _name(self, guild, uid: int) -> str:
        m = guild.get_member(uid) if guild else None
        if m:
            return m.display_name
        u = self.bot.get_user(uid)
        return (u.global_name or u.name) if u else f"User {uid}"

    def _sprite_url(self, defn: dict, phase: "dict | None", boss) -> "str | None":
        api = self.bot.config.dashboard_api_base_url
        sid = (phase or {}).get("sprite_id") or defn.get("default_sprite_id")
        if sid and api:
            return f"{api}/api/raids/sprite/{sid}"
        if boss is not None:
            return (boss.art.get("0") if boss.art else None) or boss.face
        return None

    def _boss_combatant(self, defn: dict, phase: "dict | None", current_hp: int, total_hp: int):
        battle_hp = int((phase or {}).get("battle_hp") or defn["battle_hp"])
        sid = defn.get("boss_servant_id") or 0
        boss = self.bot.servants.get(sid) if sid else None
        if sid and boss is None:
            return None, None  # a servant id was configured but doesn't exist
        if boss is None:  # fully custom boss (no servant): synthesize from configured ATK/class
            boss = _CustomBoss(defn.get("display_name", "Boss"), defn.get("boss_class", ""),
                               defn.get("boss_atk", 10000), battle_hp)
        if phase and phase.get("kit"):
            kit = Skill.from_dict({**phase["kit"], "id": getattr(boss, "id", 0)})
        else:
            kit = self.bot.kits.get(sid) if (sid and self.bot.kits) else None
        c = engine.combatant(boss, int(defn.get("boss_level", 120)), 0, kit=kit)
        c["max_hp"] = c["current_hp"] = battle_hp
        c["atk"] = int(c["atk"] * float((phase or {}).get("atk_mult", 1)))
        c["name"] = (phase or {}).get("name") or defn.get("display_name") or c["name"]
        return c, boss

    @staticmethod
    def _pool_bar(cur: int, mx: int, length: int = 20) -> str:
        if mx <= 0:
            return "\N{LIGHT SHADE}" * length
        filled = max(0, min(length, round(length * max(0, cur) / mx)))
        return "\N{DARK SHADE}" * filled + "\N{LIGHT SHADE}" * (length - filled)

    def _boss_scale(self, defn: dict, phase: "dict | None") -> float:
        """The boss's render scale: the phase override, else the def's boss_scale, else 1.5 (bosses
        loom larger than player units by default; set boss_scale=1 for a regular-sized boss)."""
        return float((phase or {}).get("sprite_scale") or defn.get("boss_scale", 1.5) or 1.5)

    async def _boss_sprite_bytes(self, session, defn, phase, boss_servant) -> "bytes | None":
        """Image bytes for the boss: the uploaded phase/default sprite (BLOB) if set, else the
        servant's full-body/art sprite."""
        sid = (phase or {}).get("sprite_id") or defn.get("default_sprite_id")
        if sid:
            data = await self.bot.raids.get_sprite(int(sid))
            if data:
                return bytes(data)
        if boss_servant is not None:
            url, _ = self._ab()._sprite_url(boss_servant)
            if url:
                try:
                    return await images.fetch_bytes(session, url)
                except Exception:
                    return None
        return None

    async def _raid_image(self, defn, phase, boss_servant, team_ids) -> "discord.File | None":
        """Composite scene: player team (left) vs the boss (right), the boss drawn at boss_scale so
        it looms larger than the units. None on any hiccup (the image is cosmetic; caller falls back)."""
        session = self.bot.http_session
        if not session:
            return None
        bgs = autobattle.pvp_backgrounds()
        bg_url = random.choice(bgs).get("bg_image") if bgs else None
        if not bg_url:
            return None
        try:
            bg = await images.fetch_bytes(session, bg_url)
            left = await self._ab()._sprites(session, team_ids)   # [(bytes, is_face)]
            boss = await self._boss_sprite_bytes(session, defn, phase, boss_servant)
            right = [(boss, False, self._boss_scale(defn, phase))] if boss else []
            png = images.battle_preview(bg, left, right)
            return discord.File(io.BytesIO(png), filename="raid.png")
        except Exception:
            return None

    # ---- /raid : fight the active boss ----
    @app_commands.command(name="raid", description="Fight the active raid boss with your /ab team.")
    @app_commands.guild_only()
    async def raid(self, interaction: discord.Interaction) -> None:
        if not self._allowed(interaction.user.id):
            return await interaction.response.send_message(_DENY, ephemeral=True)
        gid, uid = interaction.guild_id, interaction.user.id
        inst = await self.bot.raids.active(gid)
        if not inst:
            return await interaction.response.send_message("No raid is active right now.", ephemeral=True)
        got = await self.bot.raids.get_def(inst["def_name"])
        if not got:
            return await interaction.response.send_message("This raid's definition is missing.", ephemeral=True)
        defn = got[0]

        team_ids = await self.bot.contracts.battle_team(gid, uid)
        if not team_ids:
            return await interaction.response.send_message("Set a team first with /ab team.", ephemeral=True)
        # daily attempt cap + short cooldown (so no one solo-nukes the pool)
        if await self.bot.contracts.ab_reward_count(gid, uid, "raid") >= R.RAID_ATTEMPT_DAILY_CAP:
            return await interaction.response.send_message(
                f"You've hit today's raid cap ({R.RAID_ATTEMPT_DAILY_CAP} fights). Back tomorrow!",
                ephemeral=True,
            )
        now = time.monotonic()
        if now - self._cd.get((gid, uid), 0.0) < R.RAID_FIGHT_COOLDOWN:
            wait = int(R.RAID_FIGHT_COOLDOWN - (now - self._cd.get((gid, uid), 0.0))) + 1
            return await interaction.response.send_message(f"On cooldown -- try again in {wait}s.", ephemeral=True)
        self._cd[(gid, uid)] = now

        ab = self._ab()
        phase = R.active_phase(defn, inst["current_hp"], inst["total_hp"])
        boss, boss_servant = self._boss_combatant(defn, phase, inst["current_hp"], inst["total_hp"])
        if boss is None:
            return await interaction.response.send_message("The boss servant is unavailable.", ephemeral=True)
        await interaction.response.defer()

        player_team = await ab._side(gid, uid, team_ids)
        if not player_team:
            return await interaction.followup.send("Your team's servants are unavailable.")
        battle_hp = boss["max_hp"]
        state = engine.build_state(player_team, [boss])
        initial = {c["servant_id"]: c.get("equipped_item") for c in player_team}
        engine.resolve(state)
        item_lines = await ab._consume_items(gid, uid, player_team, initial)

        dmg = min(battle_hp, battle_hp - max(0, state["enemy_servants"][0]["current_hp"]))
        remaining, defeated = await self.bot.raids.apply_damage(inst["id"], uid, dmg)
        if remaining < 0:  # raced to defeat/expiry between the active() read and now
            return await interaction.followup.send("The raid just ended.")

        # per-fight QP within the daily cap
        per_fight = int(defn.get("rewards", {}).get("per_fight_qp", 0))
        reward_bits = []
        if per_fight > 0:
            await self.bot.scoring.add_qp(gid, uid, per_fight)
            reward_bits.append(f"+{qp(per_fight)}")
        await self.bot.contracts.bump_ab_reward(gid, uid, "raid")

        mine = await self.bot.raids.participation(inst["id"], uid)
        total_mine = mine["damage"] if mine else dmg
        reward_bits.append(f"**{dmg:,}** damage (your total: {total_mine:,})")

        field_ids = [c["servant_id"] for c in player_team]
        battle_file = await self._raid_image(defn, phase, boss_servant, field_ids)

        def render(log_name, log_text):
            e = self._fight_embed(defn, phase, boss_servant, state, remaining, inst["total_hp"],
                                  defeated, item_lines, reward_bits, has_image=battle_file is not None)
            if log_name is not None:
                e.add_field(name=log_name, value=log_text or "No battle log.", inline=False)
            return e

        pager = LogPager(render, state["battle_log"])
        kwargs = {"file": battle_file} if battle_file else {}
        if pager.pages > 1:
            kwargs["view"] = pager
        await interaction.followup.send(embed=pager.page_embed(), **kwargs)

        if defeated:
            fresh = await self.bot.raids.instance(inst["id"])
            await self._distribute_rewards(fresh, defeated=True, channel=interaction.channel)

    def _fight_embed(self, defn, phase, boss_servant, state, remaining, total_hp, defeated,
                     item_lines, reward_bits, has_image=False) -> discord.Embed:
        ab = self._ab()
        title = "\N{FIRE} RAID DEFEATED!" if defeated else f"\N{CROSSED SWORDS} {defn.get('display_name','Raid')}"
        color = discord.Color.gold() if defeated else discord.Color.dark_red()
        phase_name = (phase or {}).get("name")
        flavor = (phase or {}).get("flavor")
        desc = f"*{flavor}*\n\n" if flavor else ""
        desc += f"**Boss HP:** {max(0,remaining):,} / {total_hp:,}\n`{self._pool_bar(remaining, total_hp)}`"
        embed = discord.Embed(title=title, description=desc, color=color)
        if phase_name:
            embed.set_author(name=phase_name)
        if has_image:  # the composite scene (boss drawn larger); else fall back to a sprite thumbnail
            embed.set_image(url="attachment://raid.png")
        else:
            sprite = self._sprite_url(defn, phase, boss_servant)
            if sprite:
                embed.set_thumbnail(url=sprite)
        embed.add_field(name="Your Team", value=ab._team_status(state["player_servants"]), inline=True)
        embed.add_field(name="Boss", value=ab._team_status(state["enemy_servants"]), inline=True)
        if reward_bits:
            embed.add_field(name="Your strike", value=" \N{MIDDLE DOT} ".join(reward_bits), inline=False)
        if item_lines:
            embed.add_field(name="Items used", value="\n".join(item_lines), inline=False)
        return embed

    # ---- rewards ----
    @staticmethod
    def _accum(dst: dict, src: dict) -> None:
        for k in ("qp", "embers", "grails", "tickets", "hellfire"):
            if src.get(k):
                dst[k] = dst.get(k, 0) + int(src[k])

    async def _pay(self, gid: int, uid: int, r: dict) -> None:
        if r.get("qp"):
            await self.bot.scoring.add_qp(gid, uid, int(r["qp"]))
        if r.get("grails"):
            await self.bot.contracts.grant_grails(gid, uid, int(r["grails"]))
        if r.get("tickets"):
            await self.bot.contracts.grant_tickets(gid, uid, int(r["tickets"]))
        if r.get("embers"):
            await self.bot.contracts.grant_xp_item(gid, uid, "embers", int(r["embers"]))
        if r.get("hellfire"):
            await self.bot.contracts.grant_xp_item(gid, uid, "hellfire", int(r["hellfire"]))

    async def _distribute_rewards(self, inst, defeated: bool, channel=None) -> None:
        """Pay participation + ranked + last-hit once per user (guarded by raid_participation.rewarded)."""
        if inst is None:
            return
        got = await self.bot.raids.get_def(inst["def_name"])
        rewards = (got[0].get("rewards", {}) if got else {})
        ranks = rewards.get("ranks", []) if isinstance(rewards.get("ranks"), list) else []
        last_hit_by = inst.get("last_hit_by")
        parts = [p for p in await self.bot.raids.all_participants(inst["id"]) if p["damage"] > 0]
        gid = inst["guild_id"]
        paid_lines = []
        for rank, p in enumerate(parts, 1):
            uid = p["user_id"]
            if not await self.bot.raids.mark_rewarded(inst["id"], uid):
                continue  # already paid (e.g. a prior partial run)
            bundle: dict = {}
            self._accum(bundle, rewards.get("participation", {}))
            tier = min((t for t in ranks if rank <= int(t.get("top", 0))),
                       key=lambda t: int(t.get("top", 1 << 30)), default=None)
            if tier:
                self._accum(bundle, tier)
            if defeated and uid == last_hit_by:
                self._accum(bundle, rewards.get("last_hit", {}))
            if bundle:
                await self._pay(gid, uid, bundle)
                paid_lines.append(f"#{rank} <@{uid}> ({p['damage']:,} dmg)")
        ann = channel
        if ann is None and inst.get("channel_id"):
            ann = self.bot.get_channel(inst["channel_id"])
        if ann is not None:
            head = ("\N{FIRE} **" + inst.get("display_name", "The raid") + "** was defeated!"
                    if defeated else "\N{HOURGLASS} **" + inst.get("display_name", "The raid") + "** has ended.")
            top = "\n".join(paid_lines[:10]) or "No contributors."
            try:
                await ann.send(embed=discord.Embed(title=head, description="Rewards paid to:\n" + top,
                                                   color=discord.Color.gold()),
                               allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    # ---- /raidstatus ----
    @app_commands.command(name="raidstatus", description="Show the active raid's HP, phase, and leaderboard.")
    @app_commands.guild_only()
    async def raidstatus(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id
        inst = await self.bot.raids.active(gid)
        if not inst:
            return await interaction.response.send_message("No raid is active right now.", ephemeral=True)
        got = await self.bot.raids.get_def(inst["def_name"])
        defn = got[0] if got else {}
        phase = R.active_phase(defn, inst["current_hp"], inst["total_hp"])
        boss = self.bot.servants.get(inst["boss_id"])
        lb = await self.bot.raids.leaderboard(inst["id"], R.RAID_RANK_LIMIT)
        medals = {1: "\N{FIRST PLACE MEDAL}", 2: "\N{SECOND PLACE MEDAL}", 3: "\N{THIRD PLACE MEDAL}"}
        rows = "\n".join(
            f"{medals.get(i, f'{i}.')} {self._name(interaction.guild, r['user_id'])} -- {r['damage']:,}"
            for i, r in enumerate(lb, 1)
        ) or "No contributions yet."
        desc = (f"*{(phase or {}).get('flavor')}*\n\n" if phase and phase.get("flavor") else "")
        desc += (f"**HP:** {max(0,inst['current_hp']):,} / {inst['total_hp']:,}\n"
                 f"`{self._pool_bar(inst['current_hp'], inst['total_hp'])}`\n"
                 f"**Phase:** {(phase or {}).get('name', 'Opening')} \N{MIDDLE DOT} **Ends in:** {_time_left(inst['expires_at'])}")
        embed = discord.Embed(title=f"\N{CROSSED SWORDS} {inst.get('display_name','Raid')}", description=desc,
                              color=discord.Color.dark_red())
        sprite = self._sprite_url(defn, phase, boss)
        if sprite:
            embed.set_thumbnail(url=sprite)
        embed.add_field(name="Top contributors", value=rows, inline=False)
        await interaction.response.send_message(embed=embed)

    # ---- /raidhistory ----
    @app_commands.command(name="raidhistory", description="Browse past raids and their final leaderboards.")
    @app_commands.guild_only()
    async def raidhistory(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.raids.history(interaction.guild_id, 25)
        if not rows:
            return await interaction.response.send_message("No past raids yet.", ephemeral=True)
        view = RaidHistoryView(self, interaction.guild, rows)
        await interaction.response.send_message(embed=await view.render(rows[0]["id"]), view=view, ephemeral=True)

    # ---- mod: start / end ----
    @app_commands.command(name="raidstart", description="(Mods) Start an enabled raid definition here.")
    @app_commands.describe(definition="Which raid definition to start")
    @app_commands.guild_only()
    async def raidstart(self, interaction: discord.Interaction, definition: str) -> None:
        if not self._is_mod(interaction.user):
            return await interaction.response.send_message("You need mod permissions.", ephemeral=True)
        gid = interaction.guild_id
        got = await self.bot.raids.get_def(definition)
        if not got:
            return await interaction.response.send_message("No such raid definition.", ephemeral=True)
        defn, enabled = got
        if not enabled:
            return await interaction.response.send_message("That raid is disabled -- enable it first.", ephemeral=True)
        errs = R.validate_raid_def(defn)
        if errs:
            return await interaction.response.send_message("Definition is invalid: " + "; ".join(errs[:3]), ephemeral=True)
        expires = (dt.datetime.now(_UTC) + dt.timedelta(hours=int(defn["duration_hours"]))).strftime("%Y-%m-%d %H:%M:%S")
        iid = await self.bot.raids.start(gid, definition, defn.get("display_name", definition),
                                         int(defn.get("boss_servant_id") or 0), int(defn["total_hp"]), expires,
                                         channel_id=interaction.channel_id)
        if iid is None:
            return await interaction.response.send_message("A raid is already active -- /raidend it first.", ephemeral=True)
        await self.bot.raids.audit(interaction.user.id, "raid_start", {"def": definition, "instance": iid})
        boss = self.bot.servants.get(int(defn.get("boss_servant_id") or 0))
        phase = R.active_phase(defn, int(defn["total_hp"]), int(defn["total_hp"]))
        embed = discord.Embed(
            title=f"\N{CROSSED SWORDS} A raid appears: {defn.get('display_name', definition)}!",
            description=(f"**{int(defn['total_hp']):,} HP** \N{MIDDLE DOT} ends in {int(defn['duration_hours'])}h\n"
                         "Fight it with **/raid** (your /ab team). Check progress with **/raidstatus**."),
            color=discord.Color.gold(),
        )
        sprite = self._sprite_url(defn, phase, boss)
        if sprite:
            embed.set_image(url=sprite)
        await interaction.response.send_message(embed=embed)

    @raidstart.autocomplete("definition")
    async def _ac_def(self, interaction, current: str):
        q = current.strip().lower()
        out = []
        for name, defn, enabled in await self.bot.raids.all_defs():
            if not enabled:
                continue
            label = defn.get("display_name", name)
            if q and q not in name.lower() and q not in label.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=name))
            if len(out) >= 25:
                break
        return out

    @app_commands.command(name="raidend", description="(Mods) Force-end the active raid.")
    @app_commands.guild_only()
    async def raidend(self, interaction: discord.Interaction) -> None:
        if not self._is_mod(interaction.user):
            return await interaction.response.send_message("You need mod permissions.", ephemeral=True)
        inst = await self.bot.raids.active(interaction.guild_id)
        if not inst:
            return await interaction.response.send_message("No raid is active.", ephemeral=True)
        await self.bot.raids.end(inst["id"], "expired")
        await self.bot.raids.audit(interaction.user.id, "raid_end", {"instance": inst["id"]})
        await self._distribute_rewards(await self.bot.raids.instance(inst["id"]), defeated=False,
                                       channel=interaction.channel)
        await interaction.response.send_message("Raid ended; participation rewards paid.", ephemeral=True)

    # ---- expiry ticker ----
    @tasks.loop(minutes=R.RAID_EXPIRY_TICK)
    async def _expiry(self) -> None:
        try:
            for inst in await self.bot.raids.expired():
                await self.bot.raids.end(inst["id"], "expired")
                await self._distribute_rewards(await self.bot.raids.instance(inst["id"]), defeated=False)
        except Exception:  # never let the ticker die
            pass

    @_expiry.before_loop
    async def _before_expiry(self) -> None:
        await self.bot.wait_until_ready()


class RaidHistoryView(discord.ui.View):
    """Ephemeral past-raids browser: a dropdown of ended raids; picking one shows its final leaderboard.
    Mirrors WarHistoryView."""

    def __init__(self, cog: Raids, guild, rows) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.guild = guild
        self.rows = {r["id"]: r for r in rows}
        options = []
        for r in rows[:25]:
            outcome = "defeated" if r["status"] == "defeated" else "expired"
            options.append(discord.SelectOption(
                label=(r["display_name"] or r["def_name"] or "Raid")[:90],
                description=f"{outcome} \N{MIDDLE DOT} {(r['ended_at'] or '')[:10]}"[:100],
                value=str(r["id"]),
            ))
        self.select = discord.ui.Select(placeholder="Pick a past raid", options=options)
        self.select.callback = self._on_pick
        self.add_item(self.select)

    async def render(self, instance_id: int) -> discord.Embed:
        r = self.rows[instance_id]
        lb = await self.cog.bot.raids.leaderboard(instance_id, 10)
        medals = {1: "\N{FIRST PLACE MEDAL}", 2: "\N{SECOND PLACE MEDAL}", 3: "\N{THIRD PLACE MEDAL}"}
        rows = "\n".join(
            f"{medals.get(i, f'{i}.')} {self.cog._name(self.guild, x['user_id'])} -- {x['damage']:,}"
            for i, x in enumerate(lb, 1)
        ) or "No contributors."
        outcome = "\N{FIRE} Defeated" if r["status"] == "defeated" else "\N{HOURGLASS} Expired"
        embed = discord.Embed(
            title=f"{r['display_name'] or r['def_name']}",
            description=f"{outcome} \N{MIDDLE DOT} {(r['ended_at'] or '')[:10]}",
            color=discord.Color.dark_gold(),
        )
        embed.add_field(name="Final leaderboard", value=rows, inline=False)
        return embed

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=await self.render(int(self.select.values[0])), view=self)


async def setup(bot) -> None:
    await bot.add_cog(Raids(bot))
