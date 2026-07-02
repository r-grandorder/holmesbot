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
from data.servants import ServantIndex
from data.shadows import ShadowCatalog
from db import Database
from services.aliases import AliasService
from services.games import GameService
from services.guild_config import GuildConfigService
from services.restrictions import RestrictionService
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
        self.shadows: ShadowCatalog | None = None
        self.scoring: ScoringService | None = None
        self.aliases: AliasService | None = None
        self.restrictions: RestrictionService | None = None
        self.guild_config: GuildConfigService | None = None
        self.games: GameService | None = None
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        await self.db.connect()
        assert self.db.pool is not None
        self.http_session = aiohttp.ClientSession()
        self.servants = ServantIndex.load()
        self.shadows = ShadowCatalog.load()
        host.resolve_portraits(self.servants)
        log.info("loaded %d servants, %d shadow assets", len(self.servants), len(self.shadows))

        self.scoring = ScoringService(self.db.pool)
        self.restrictions = RestrictionService(self.db.pool)
        self.guild_config = GuildConfigService(self.db.pool)
        self.games = GameService(self.db.pool)
        self.aliases = AliasService(self.db.pool)
        await self.aliases.reload()
        await self.games.sweep_expired()

        await self._start_health_server()

        branding.configure(self.config.qp_emote)
        for ext in COGS:
            await self.load_extension(ext)

        if self.config.guild_ids:
            # Register directly in our guild(s) for instant command updates.
            for guild_id in self.config.guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            # Drop any lingering global registrations so commands don't appear twice.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.info("synced commands to %d guild(s)", len(self.config.guild_ids))
        else:
            await self.tree.sync()
            log.info("synced commands globally")

    async def _start_health_server(self) -> None:
        async def health(_request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app = web.Application()
        app.router.add_get("/health", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.config.health_port).start()
        self._health_runner = runner
        log.info("health server listening on :%d", self.config.health_port)

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
