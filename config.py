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
            repost_after=int(os.environ.get("REPOST_AFTER") or "0"),
            next_vote_seconds=int(os.environ.get("NEXT_VOTE_SECONDS") or "0"),
            contract_open=contract_open,
            contract_whitelist=contract_whitelist,
            contract_summon_cost=int(os.environ.get("CONTRACT_SUMMON_COST") or "100"),
            shop_grail_cost=int(os.environ.get("SHOP_GRAIL_COST") or "10000"),
            shop_ticket_cost=int(os.environ.get("SHOP_TICKET_COST") or "100000"),
            summon_ticket_wish_chance=float(os.environ.get("SUMMON_TICKET_WISH_CHANCE") or "0.15"),
            levelup_announce=levelup_announce,
        )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
