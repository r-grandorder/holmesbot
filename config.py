from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Guilds to register commands in directly (instant sync) when GUILD_IDS is unset.
# Add more here (or via the GUILD_IDS env var) as the bot gets installed elsewhere.
DEFAULT_GUILD_IDS = (1199866819322855568,)  # JC's personal server


@dataclass(frozen=True)
class Config:
    discord_token: str
    application_id: int
    database_url: str
    client_secret: str | None
    guild_ids: tuple[int, ...]
    health_port: int
    log_level: str
    assets_base_url: str | None
    qp_emote: str
    grail_emote: str
    summon_ticket_emote: str
    ember_emote: str
    hellfire_emote: str
    repost_after: int
    # Seconds the post-reveal "next round" vote stays open. 0 disables it entirely
    # (reveals keep the plain Play Again button), so the feature ships dark.
    next_vote_seconds: int
    # Contracted-servant feature (ships dark). CONTRACT_WHITELIST is the master switch: empty
    # = off (cog/commands/listener never registered); a boolean (true/all/1/on/yes) = open to
    # everyone in the server (contract_open); a list of user IDs = only those testers.
    # summon_cost is the QP per roll.
    contract_open: bool
    contract_whitelist: frozenset[int]
    contract_summon_cost: int
    # QP shop prices (/shop).
    shop_grail_cost: int
    shop_ticket_cost: int
    # A Summon Ticket's chance (0..1) to pull your /wish target, else a guaranteed 5-star.
    summon_ticket_wish_chance: float
    # How contract level-up pings behave: "off" | "milestones" (every Nth level + at cap) | "all".
    levelup_announce: str
    # Autobattle is experimental; its cog (commands + team config) only loads when this is on.
    autobattle_enabled: bool
    # The garden (/water, /mulch, /height). Ships dark: the cog only loads when this is on.
    # water_qp_reward is paid to the WATERER, at most once per water_qp_cooldown_hours, so a
    # player's income is a fixed rate no matter how many people they water. Note this is by far
    # the largest QP faucet at the default 5000/12h -- roughly a shop grail per day per active
    # player -- so tune it here rather than reworking sink prices.
    water_enabled: bool
    water_qp_reward: int
    water_qp_cooldown_hours: float
    shop_fertilizer_cost: int
    # Automated SQLite -> S3 backups. Ships dark: the backup task only runs when a bucket is set.
    # Must be a PRIVATE bucket (never the public assets bucket). AWS creds come from the standard
    # boto3 env chain (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION).
    backup_s3_bucket: str
    backup_s3_prefix: str
    backup_interval_hours: int
    backup_retention: int
    # Read-only web dashboard (Discord-OAuth). Ships dark: mounted on the :8080 app only when all
    # of session_secret + api_base_url + frontend_url + DISCORD_CLIENT_SECRET are set. api_base_url
    # is the API's public URL (the Cloudflare Tunnel hostname), frontend_url is where the SPA lives
    # (also the allowed CORS origin), guild_id scopes it (defaults to the first configured guild).
    dashboard_session_secret: str
    dashboard_api_base_url: str
    dashboard_frontend_url: str
    dashboard_guild_id: int
    # Discord user ids allowed to use the dashboard's write features (the kit editor). Explicit
    # allowlist -- kit edits are powerful, so we gate on named ids rather than a Discord role.
    dashboard_mod_ids: frozenset[int]

    @property
    def dashboard_enabled(self) -> bool:
        return bool(
            self.dashboard_session_secret
            and self.dashboard_api_base_url
            and self.dashboard_frontend_url
            and self.client_secret
        )

    @classmethod
    def from_env(cls) -> "Config":
        guild_ids_raw = os.environ.get("GUILD_IDS", "").strip()
        guild_ids = tuple(int(g.strip()) for g in guild_ids_raw.split(",") if g.strip())
        if not guild_ids:
            guild_ids = DEFAULT_GUILD_IDS
        levelup_announce = (os.environ.get("LEVELUP_ANNOUNCE") or "milestones").strip().lower()
        if levelup_announce not in ("off", "milestones", "all"):
            levelup_announce = "milestones"
        raw_whitelist = (os.environ.get("CONTRACT_WHITELIST") or "").strip()
        contract_open = raw_whitelist.lower() in ("true", "1", "yes", "on", "all", "*", "enabled")
        contract_whitelist = (
            frozenset()
            if contract_open
            else frozenset(
                int(x)
                for x in raw_whitelist.replace(",", " ").split()
                if x.isdigit() and int(x) > 0
            )
        )
        return cls(
            discord_token=_require("DISCORD_BOT_TOKEN"),
            application_id=int(_require("DISCORD_APPLICATION_ID")),
            database_url=_require("DATABASE_URL"),
            client_secret=os.environ.get("DISCORD_CLIENT_SECRET") or None,
            guild_ids=guild_ids,
            health_port=int(os.environ.get("HEALTH_PORT", "8080")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            assets_base_url=(os.environ.get("ASSETS_BASE_URL") or "").rstrip("/") or None,
            qp_emote=os.environ.get("QP_EMOTE", "QP"),
            grail_emote=os.environ.get("GRAIL_EMOTE", ""),
            summon_ticket_emote=os.environ.get("SUMMON_TICKET_EMOTE", ""),
            ember_emote=os.environ.get("EMBER_EMOTE", ""),
            hellfire_emote=os.environ.get("HELLFIRE_EMOTE", ""),
            repost_after=int(os.environ.get("REPOST_AFTER") or "0"),
            next_vote_seconds=int(os.environ.get("NEXT_VOTE_SECONDS") or "0"),
            contract_open=contract_open,
            contract_whitelist=contract_whitelist,
            contract_summon_cost=int(os.environ.get("CONTRACT_SUMMON_COST") or "100"),
            shop_grail_cost=int(os.environ.get("SHOP_GRAIL_COST") or "10000"),
            shop_ticket_cost=int(os.environ.get("SHOP_TICKET_COST") or "100000"),
            summon_ticket_wish_chance=float(os.environ.get("SUMMON_TICKET_WISH_CHANCE") or "0.15"),
            levelup_announce=levelup_announce,
            autobattle_enabled=(os.environ.get("AUTOBATTLE_ENABLED") or "").strip().lower()
            in ("1", "true", "yes", "on", "enabled"),
            water_enabled=(os.environ.get("WATER_ENABLED") or "").strip().lower()
            in ("1", "true", "yes", "on", "enabled"),
            water_qp_reward=int(os.environ.get("WATER_QP_REWARD") or "5000"),
            water_qp_cooldown_hours=float(os.environ.get("WATER_QP_COOLDOWN_HOURS") or "12"),
            shop_fertilizer_cost=int(os.environ.get("SHOP_FERTILIZER_COST") or "2500"),
            backup_s3_bucket=(os.environ.get("BACKUP_S3_BUCKET") or "").strip(),
            backup_s3_prefix=(os.environ.get("BACKUP_S3_PREFIX") or "db-backups/").strip(),
            backup_interval_hours=int(os.environ.get("BACKUP_INTERVAL_HOURS") or "6"),
            backup_retention=int(os.environ.get("BACKUP_RETENTION") or "30"),
            dashboard_session_secret=(os.environ.get("DASHBOARD_SESSION_SECRET") or "").strip(),
            dashboard_api_base_url=(os.environ.get("DASHBOARD_API_BASE_URL") or "").strip().rstrip("/"),
            dashboard_frontend_url=(os.environ.get("DASHBOARD_FRONTEND_URL") or "").strip().rstrip("/"),
            dashboard_guild_id=int(os.environ.get("DASHBOARD_GUILD_ID") or "0"),
            dashboard_mod_ids=frozenset(
                int(x) for x in (os.environ.get("DASHBOARD_MOD_IDS") or "").replace(",", " ").split()
                if x.isdigit() and int(x) > 0
            ),
        )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
