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

    @classmethod
    def from_env(cls) -> "Config":
        guild_ids_raw = os.environ.get("GUILD_IDS", "").strip()
        guild_ids = tuple(int(g.strip()) for g in guild_ids_raw.split(",") if g.strip())
        if not guild_ids:
            guild_ids = DEFAULT_GUILD_IDS
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
        )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
