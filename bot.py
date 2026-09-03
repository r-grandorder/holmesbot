from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands

import branding
from config import Config
from data import host
from data.ce import CeIndex
from data.kits import KitIndex, Skill
from data.custom_servants import to_index_item
from data.servants import ServantIndex
from data.shadows import ShadowCatalog
from db import Database
from services.aliases import AliasService
from services.backup import BackupService
from services.kits import KitService
from services.raids import RaidService
from services.contracts import ContractService
from services.custom_servants import CustomServantService
from services.games import GameService
from services.garden import GardenService
from services.guild_config import GuildConfigService
from services.restrictions import RestrictionService
from services.wars import WarService
from services.scoring import ScoringService

log = logging.getLogger("holmesbot")

COGS = (
    "cogs.guess_servant",
    "cogs.guess_shadow",
    "cogs.guess_audio",
    "cogs.chat_guess",
    "cogs.economy",
    "cogs.admin",
    "cogs.guess_random",
    "cogs.guess_skill",
    "cogs.guess_ce",
)


class HolmesBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # players guess by typing in chat
        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=config.application_id,
        )
        self.config = config
        # channel_id -> in-flight ChatRound (single gateway process, so in-memory
        # is safe; the active_games table is the durable backstop for expiry).
        self.active_rounds: dict[int, object] = {}
        # channel ids mid-launch: a synchronous reservation so two near-simultaneous
        # starts (e.g. racing Play Again clicks) can't both spawn a round.
        self.launching: set[int] = set()
        # vote-message id -> ChatRound awaiting give-up reactions.
        self.forfeit_votes: dict[int, object] = {}
        # channel id -> deque of recent servant ids, to avoid back-to-back repeats.
        self.recent_picks: dict[int, object] = {}
        self.db = Database(config.database_url)
        self.http_session: aiohttp.ClientSession | None = None
        self.servants: ServantIndex | None = None
        self.kits: KitIndex | None = None
        self.ces: CeIndex | None = None
        self.shadows: ShadowCatalog | None = None
        self.scoring: ScoringService | None = None
        self.aliases: AliasService | None = None
        self.restrictions: RestrictionService | None = None
        self.guild_config: GuildConfigService | None = None
        self.games: GameService | None = None
        self.contracts: ContractService | None = None
        self.custom_servants: CustomServantService | None = None
        self.garden: GardenService | None = None
        self.backups: BackupService | None = None
        self.kit_service: KitService | None = None
        self.raids: RaidService | None = None
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        await self.db.connect()
        assert self.db.pool is not None
        self.http_session = aiohttp.ClientSession()
        self.servants = ServantIndex.load()
        self.ces = CeIndex.load()
        self.shadows = ShadowCatalog.load()
        # Layer live, mod-edited custom units over the baked index (data/custom_servants.json
        # stays the seed). Done BEFORE resolve_portraits so a custom host portrait resolves too.
        self.custom_servants = CustomServantService(self.db.pool)
        customs = await self._apply_custom_servants()
        host.resolve_portraits(self.servants)
        log.info(
            "loaded %d servants (%d live custom), %d craft essences, %d shadow assets",
            len(self.servants), customs, len(self.ces), len(self.shadows),
        )

        self.scoring = ScoringService(self.db.pool)
        self.restrictions = RestrictionService(self.db.pool)
        self.guild_config = GuildConfigService(self.db.pool)
        self.games = GameService(self.db.pool)
        self.aliases = AliasService(self.db.pool)
        self.contracts = ContractService(self.db.pool)
        self.wars = WarService(self.db.pool)
        self.raids = RaidService(self.db.pool)
        self.garden = GardenService(self.db.pool)
        await self.aliases.reload()
        await self.games.sweep_expired()

        await self._start_health_server()

        branding.configure(self.config.qp_emote)
        for ext in COGS:
            await self.load_extension(ext)
        # Contracted-servant feature ships dark: register it when open to all or whitelisted.
        if self.config.contract_open or self.config.contract_whitelist:
            await self.load_extension("cogs.contracts")
            log.info(
                "contracted-servant feature enabled (%s)",
                "whole server"
                if self.config.contract_open
                else f"{len(self.config.contract_whitelist)} user(s)",
            )
        # The garden ships dark: /water, /mulch and /height only register when WATER_ENABLED is set.
        if self.config.water_enabled:
            await self.load_extension("cogs.garden")
            log.info(
                "garden enabled (%s QP per water, one payout per %gh)",
                f"{self.config.water_qp_reward:,}", self.config.water_qp_cooldown_hours,
            )
        # Autobattle is experimental; its cog only loads when AUTOBATTLE_ENABLED is set.
        if self.config.autobattle_enabled:
            self.kits = KitIndex.load()
            # Layer any live, mod-edited kit overrides (data/kits/*.json stays the baked seed).
            self.kit_service = KitService(self.db.pool)
            overrides = await self.kit_service.all_overrides()
            for sid, kit in overrides:
                try:
                    self.kits.set(int(sid), Skill.from_dict(kit))
                except Exception:
                    log.warning("skipping bad kit override for %s", sid)
            await self.load_extension("cogs.autobattle")
            try:
                await self.load_extension("cogs.raids")
            except Exception:
                # Raids are new/untested -- never let a load failure crash-loop the whole bot.
                log.exception("failed to load cogs.raids; raids disabled this boot")
            log.info(
                "autobattle feature enabled (%d kits, %d overrides)",
                len(self.kits), len(overrides),
            )

        # Automated DB backups ship dark: only when a (private) S3 bucket is configured.
        if self.config.backup_s3_bucket:
            self.backups = BackupService(
                self.db.pool,
                bucket=self.config.backup_s3_bucket,
                prefix=self.config.backup_s3_prefix,
                retention=self.config.backup_retention,
            )
            await self.load_extension("cogs.backup")
            log.info(
                "db backups enabled -> s3://%s/%s every %dh, keeping %d",
                self.config.backup_s3_bucket,
                self.config.backup_s3_prefix,
                self.config.backup_interval_hours,
                self.config.backup_retention,
            )

        if self.config.guild_ids:
            # Register directly in our guild(s) for instant command updates. A guild the bot
            # has NOT been invited to yet raises on sync -- skip it with a warning rather than
            # crash startup and take down every other (valid) guild.
            synced = 0
            for guild_id in self.config.guild_ids:
                guild = discord.Object(id=guild_id)
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    synced += 1
                except discord.HTTPException as exc:
                    log.warning(
                        "skipping command sync for guild %s (not invited or no access?): %s",
                        guild_id,
                        exc,
                    )
            # Drop any lingering global registrations so commands don't appear twice.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.info("synced commands to %d/%d guild(s)", synced, len(self.config.guild_ids))
        else:
            await self.tree.sync()
            log.info("synced commands globally")

    async def _start_health_server(self) -> None:
        async def health(_request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app = web.Application()
        app.router.add_get("/health", health)
        # Read-only web dashboard shares this app (and the one SQLite connection). Ships dark.
        if self.config.dashboard_enabled:
            from dashboard import setup_dashboard

            setup_dashboard(app, self)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.config.health_port).start()
        self._health_runner = runner
        log.info("health server listening on :%d", self.config.health_port)

    async def _apply_custom_servants(self) -> int:
        """Layer every ENABLED custom-servant row onto the in-memory index. Returns how many
        applied. A bad row is skipped rather than fatal -- one broken definition must not stop
        the bot booting."""
        if self.servants is None or self.custom_servants is None:
            return 0
        applied = 0
        for defn in await self.custom_servants.enabled_defs():
            try:
                self.servants.upsert(
                    ServantIndex.item_to_servant(to_index_item(defn))
                )
                applied += 1
            except Exception:
                log.warning("skipping bad custom servant %s", defn.get("id"))
        return applied

    async def reload_servants(self) -> None:
        """Re-apply DB custom-servant rows over a fresh baked load, IN PLACE, so an edit shows
        up without a restart. In place matters: every cog captured self.servants at startup,
        so rebinding the index would leave them all reading a stale copy."""
        if self.servants is None or self.custom_servants is None:
            return
        self.servants.reset(ServantIndex.load().all())
        await self._apply_custom_servants()
        host.resolve_portraits(self.servants)

    async def reload_kits(self) -> None:
        """Re-apply DB kit overrides over a fresh baked load, in place, so live battles pick up an
        edit without a restart. No-op when autobattle is off."""
        if self.kits is None or self.kit_service is None:
            return
        self.kits.reset(dict(KitIndex.load().items()))
        for sid, kit in await self.kit_service.all_overrides():
            try:
                self.kits.set(int(sid), Skill.from_dict(kit))
            except Exception:
                log.warning("skipping bad kit override for %s", sid)

    async def on_ready(self) -> None:
        log.info("connected as %s", self.user)

    async def close(self) -> None:
        if self.http_session is not None:
            await self.http_session.close()
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await self.db.close()
        await super().close()


async def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = HolmesBot(config)
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
