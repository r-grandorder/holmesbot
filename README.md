# Holmes Bot

A Fate/Grand Order guessing-game Discord bot (clean rebuild) for the r/grandorder server.
Games: `/guess_servant`, `/guess_shadow`, and an audio mode. Lifetime points + leaderboard,
no spendable currency. SQLite backend; runs as a single self-hosted Docker container.

## Run it

The bot is a single container (`ghcr.io/r-grandorder/holmesbot`) with a persistent volume
for its SQLite database; watchtower auto-updates it when CI publishes a new image.

```bash
cp .env.example .env   # Discord token, application id, ASSETS_BASE_URL
docker compose up -d
```

See [DEPLOY.md](DEPLOY.md) for the full walkthrough (GHCR access, updates, backups).

## Database

Local SQLite, no DB server. `dbmate` applies `database/migrations/` on startup. Inspect:

```bash
sqlite3 ./database/holmesbot.sqlite3 ".tables"
```

## AWS (assets only)

The only AWS dependency is an S3 bucket of precomputed `/guess_shadow` silhouettes
(public-read). The `refresh-assets` workflow writes to it via a keyless GitHub OIDC role;
the running bot only reads the public URLs (no AWS credentials). `terraform/` provisions
exactly that bucket + the OIDC role, nothing else:

```bash
cd terraform
terraform init
terraform apply
```

Then wire up the two outputs:
- `terraform output assets_base_url` -> the bot's `ASSETS_BASE_URL` env var.
- `terraform output github_ci_role_arn` -> the `AWS_DEPLOY_ROLE_ARN` repo variable (used by `refresh-assets`).

## Secrets

The bot's Discord token and other config live in the host's `.env` (see `.env.example`),
not in AWS. `terraform/terraform.tfvars` is optional now: no secrets are required to
provision the asset bucket.

## Privacy

See the [Privacy Policy](PRIVACY.md) for what the bot does and does not collect.
