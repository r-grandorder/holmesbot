# Bunyan Bot

A clean, from-scratch Fate/Grand Order guessing-game Discord bot for the r/grandorder server.
This file is the durable project context. Read it first.

## What this is
- Three guessing games: `/guess_servant` (cropped servant art), `/guess_shadow` (silhouette), and
  an audio mode (guess from voice lines). Lifetime points + a leaderboard. No spendable currency.
- Data comes from Atlas Academy (api.atlasacademy.io): servant names/aliases, ascension art, voice lines.
- Single self-hosted Docker container, SQLite backend (deploy via GHCR image + watchtower).

## Status (2026-06-29)
- Bot implemented: all three games, scoring/QP, aliases, restrictions, admin commands.
- DB migrated from Postgres to SQLite (single file on a mounted volume). Single-instance; the
  gateway-leader advisory lock is gone.
- Hosting pivot to a GHCR image + watchtower self-host is decided but NOT yet implemented: the AWS
  terraform + ECR/ECS workflows are still present and will be retired.

## Locked decisions (do not relitigate)
- **Scoring**: lifetime points + leaderboard only. No currency, no shop. A score needs no sinks. Leave a
  clean seam (a separate `spent` ledger) to add real currency later only if a genuine sink ever appears.
- **Audio mode**: flat point reward. No wager, no PvP steal (the legacy "bet"/sniper mechanic is dropped).
- **One round shape**: pick eligible servant -> present prompt -> modal guess -> fuzzy match (accent-strip
  + a-z lowercase + 1-char typo tolerance + alias table) -> award points -> reveal on win/timeout.
- **Restricted-servant/art list**: ship the mechanism, start EMPTY (JC curates later). Granularity
  `(servant_id, scope[full|ascension|costume], ascension_keys)`. Enforce at build-time index bake AND at
  runtime. Two enforcement points: the eligible pool AND the reveal asset (the shadow reveal is the colored
  figure even though the silhouette is safe). Fail-safe = exclude when in doubt. NEVER invent servant IDs.
- **Hosting**: single self-hosted Docker container (GHCR image + watchtower pull-deploys). One instance,
  so there is no gateway-leader election; a watchtower recreate is a few seconds of downtime per deploy.
  `/health` (port 8080) stays for container health checks. (Was ECS Fargate + a Postgres advisory lock.)
- **DB**: SQLite via a thin asyncpg-style shim in `db.py` (one connection, serialized). The file lives on
  a mounted volume so it survives container replacement. (Was private RDS + bastion SSH tunnel.)
- **Secrets**: live in `terraform/terraform.tfvars` (gitignored), pushed to SSM SecureString by terraform.
  JC declined token rotation; do not raise it again.
- **Future dashboard**: Cloudflare Pages SPA + a Cloudflare Tunnel to a small API (likely no ALB ever).
  Keep a service/domain layer that owns all config + moderation reads/writes (callable by cogs now and the
  API later); SQLite is the sole source of truth + an `audit_log`; Discord-OAuth per-guild staff roles.

## Architecture
- Python + discord.py. Repo root holds the bot; `terraform/` holds infra.
- Layered: thin discord.py cogs over a service/domain layer over a SQLite repository (`db.py`, an
  asyncpg-style shim). Never bury DB
  mutations inside interaction callbacks (the future dashboard API reuses the same service layer).
- Persist in-flight games in an `active_games` table (no process-global state, survives deploys).
- dbmate migrations in `database/migrations/`. Dockerfile runs `dbmate up` then `exec python bot.py`.

## Conventions
- No emojis, no em-dashes in user-facing copy (NPC/UI text). Code comments are fine.
- Admin commands are their own top-level feature-named commands, never nested under one catch-all.
- `requirements.in` is the source; `requirements.txt` is the generated lockfile (`make lock` after edits).

## Reference projects (for patterns only, not dependencies)
- Prototype being rebuilt: a legacy Replit FGO guessing bot (its game logic lived in a `gacha/` module).
- Infra/deploy patterns mirrored from a sibling AWS-hosted Discord bot project.
- FGO data + Cloudflare Tunnel patterns from a separate self-hosted FGO project.
