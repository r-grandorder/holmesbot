# Deploying Holmes Bot (self-hosted)

The bot runs as a single Docker container with a persistent volume for its SQLite
database. CI builds and pushes `ghcr.io/r-grandorder/holmesbot:latest` on every push
to `main`; [watchtower](https://containrrr.dev/watchtower/) on the host pulls new
images automatically. No AWS and no database server are needed to run it.

## Prerequisites
- A host with Docker + the Compose plugin.
- A Discord bot token + application id.
- `ASSETS_BASE_URL`: the public URL of the S3 assets bucket (silhouettes for
  `/guess_shadow`). The other games do not need it.

## First run
```bash
cp .env.example .env         # fill DISCORD_BOT_TOKEN, DISCORD_APPLICATION_ID, ASSETS_BASE_URL
docker compose up -d
docker compose logs -f bot   # watch it run migrations (dbmate) and connect
```
Migrations run automatically on start. The SQLite file lives on the `holmes-data`
volume and survives image updates.

## GHCR access
The image is `ghcr.io/r-grandorder/holmesbot`. If the package is public, no login is
needed. If it is private, log the host in once with a PAT that has `read:packages`
and uncomment the `~/.docker/config.json` mount in `docker-compose.yml` so watchtower
can pull too:
```bash
echo "$GHCR_PAT" | docker login ghcr.io -u <user> --password-stdin
```

## Updates
Push to `main` -> CI builds and pushes `:latest` -> watchtower redeploys within ~5 min
(a few seconds of downtime while the container is recreated). To update now:
```bash
docker compose pull bot && docker compose up -d bot
```

## Backups
The entire state is the SQLite file on the `holmes-data` volume. For a quick manual copy:
```bash
docker compose cp bot:/data/holmesbot.sqlite3 ./backup-$(date +%F).sqlite3
```

### Automated off-site backups (S3)
The bot can snapshot the DB and upload it to a **private** S3 bucket on a schedule. It ships dark:
nothing runs until `BACKUP_S3_BUCKET` is set. Each run takes a consistent snapshot (`VACUUM INTO`,
safe while the bot keeps writing), gzips it, uploads a timestamped object, and prunes to
`BACKUP_RETENTION` newest.

1. **Provision** the private bucket + a scoped IAM user:
   ```bash
   cd terraform && terraform apply
   terraform output backup_bucket
   terraform output backup_access_key_id
   terraform output -raw backup_secret_access_key
   ```
2. **Configure** `.env` (see `.env.example`):
   ```
   BACKUP_S3_BUCKET=<backup_bucket>
   AWS_ACCESS_KEY_ID=<backup_access_key_id>
   AWS_SECRET_ACCESS_KEY=<backup_secret_access_key>
   AWS_DEFAULT_REGION=us-east-1
   # optional: BACKUP_INTERVAL_HOURS=6  BACKUP_RETENTION=30  BACKUP_S3_PREFIX=db-backups/
   ```
3. `docker compose up -d` to pick up the new env. On boot the log shows `db backups enabled -> ...`.
   Mods can trigger one anytime with `/dbbackup`.

**Restore:** download an object, `gunzip` it, and drop it in as `/data/holmesbot.sqlite3`:
```bash
aws s3 cp s3://<bucket>/db-backups/<file>.sqlite3.gz - | gunzip > holmesbot.sqlite3
docker compose cp ./holmesbot.sqlite3 bot:/data/holmesbot.sqlite3 && docker compose restart bot
```

## Web dashboard (read-only)
An optional Discord-login dashboard for **viewing** inventory, servants, leaderboards, and wars.
The bot stays the only way to *change* anything. It ships dark: the API only mounts when all four
`DASHBOARD_*` vars (plus `DISCORD_CLIENT_SECRET`) are set. Two pieces: the API runs inside the bot
on `:8080` (same process, same SQLite connection); the SPA (`web/dashboard.html`) is served by the
existing Cloudflare Pages site. Layout (domain `sherlockholmesbot.com`):

- `https://sherlockholmesbot.com` -> Pages (kit browser at `/`, dashboard at `/dashboard.html`)
- `https://api.sherlockholmesbot.com` -> the bot's `:8080`, via a Cloudflare Tunnel

### One-time setup
0. **Domain on Cloudflare.** If bought via Cloudflare Registrar it's already there. Otherwise:
   CF dashboard -> Add a site -> `sherlockholmesbot.com` (Free) -> change the nameservers at your
   registrar to the two CF gives you, and wait for "Active". (Make sure WHOIS privacy is on.)
1. **Frontend domain (Pages).** Workers & Pages -> `holmesbot-kits` -> Custom domains -> add
   `sherlockholmesbot.com`. CF creates the DNS record + cert automatically.
2. **API tunnel.** Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared -> name it
   (e.g. `holmesbot`). Copy the **token**, put it in `.env` as `TUNNEL_TOKEN=`. Then under the
   tunnel's **Public Hostnames**, add: subdomain `api`, domain `sherlockholmesbot.com`, service
   `HTTP` `http://bot:8080` (the compose service name). CF creates the `api` DNS record.
3. **Discord OAuth.** Developer Portal -> your app -> OAuth2 -> Redirects -> add
   `https://api.sherlockholmesbot.com/api/auth/callback` -> Save.
4. **`.env`** (see `.env.example` -- the domain values are pre-filled there):
   ```
   DASHBOARD_SESSION_SECRET=<openssl rand -hex 32>
   DASHBOARD_API_BASE_URL=https://api.sherlockholmesbot.com
   DASHBOARD_FRONTEND_URL=https://sherlockholmesbot.com
   DASHBOARD_GUILD_ID=0            # 0 = first of GUILD_IDS
   TUNNEL_TOKEN=<from step 2>
   ```
5. **Bring it up** with the tunnel profile:
   ```
   docker compose --profile tunnel up -d
   ```
   Boot log shows `dashboard API mounted ...`; `curl https://api.sherlockholmesbot.com/health` -> `ok`.
6. **Ship the SPA.** `web/dashboard.html` already points `API_BASE` at `api.sherlockholmesbot.com`,
   so just let the site workflow deploy. Visit `https://sherlockholmesbot.com/dashboard.html`.

Auth is a signed bearer token (HMAC) the SPA keeps in `localStorage` -- no cookies, so no
third-party-cookie issues. Everything is read-only; the token grants no write access. With the
custom domain in place you *could* later switch to a same-site cookie, but it isn't necessary.
