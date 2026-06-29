# Bunyan Bot

A clean, from-scratch Fate/Grand Order guessing-game Discord bot for the r/grandorder server.
This file is the durable project context. Read it first.

## What this is
- Three guessing games: `/guess_servant` (cropped servant art), `/guess_shadow` (silhouette), and
  an audio mode (guess from voice lines). Lifetime points + a leaderboard. No spendable currency.
- Data comes from Atlas Academy (api.atlasacademy.io): servant names/aliases, ascension art, voice lines.
- Hosted on AWS ECS Fargate, Postgres backend, deployed on push to `main`.

## Status (2026-06-25)
- Infra slice 1 written, `terraform validate` passing: VPC (public+private, no NAT), private RDS
  (db.t4g.micro), a t4g.nano bastion with a generated key, SSM secrets. NOT yet applied.
- Bot application code: not started.

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
- **Hosting**: ECS Fargate, awsvpc, public subnet + public IP (no NAT). No ALB (ECS container healthCheck
  on `/health`). Blue/green = ECS rolling (min 100% / max 200%) + a Postgres advisory lock electing the
  Discord gateway leader. `/health` MUST return 200 while still waiting for the lock, or rollouts deadlock.
- **DB access**: private RDS + bastion (generated ED25519 key) + SSH tunnel; bare `psql` to localhost.
- **Secrets**: live in `terraform/terraform.tfvars` (gitignored), pushed to SSM SecureString by terraform.
  JC declined token rotation; do not raise it again.
- **Future dashboard**: Cloudflare Pages SPA + a Cloudflare Tunnel to a small API (likely no ALB ever).
  Keep a service/domain layer that owns all config + moderation reads/writes (callable by cogs now and the
  API later); Postgres is the sole source of truth + an `audit_log`; Discord-OAuth per-guild staff roles.

## Architecture
- Python + discord.py. Repo root holds the bot; `terraform/` holds infra.
- Layered: thin discord.py cogs over a service/domain layer over an asyncpg repository. Never bury DB
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
