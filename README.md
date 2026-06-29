# Bunyan Bot

A Fate/Grand Order guessing-game Discord bot (clean rebuild) for the r/grandorder server.
Games: `/guess_servant`, `/guess_shadow`, and an audio mode. Lifetime points + leaderboard,
no spendable currency. SQLite backend; runs as a single self-hosted Docker container.

## Layout

```
terraform/            AWS infra (Fargate, RDS, bastion, ECR, SSM, ...)
  terraform.tfvars        secrets (GITIGNORED — never commit)
  terraform.tfvars.example  template
  .secrets/               generated bastion private key (GITIGNORED)
```

Bot application code, Dockerfile, dbmate migrations, ECS/ECR, GitHub OIDC, and the
deploy-on-push workflow land in later slices.

## Infra: current slice

VPC (public + private subnets, no NAT) · private RDS Postgres (`db.t4g.micro`, Single-AZ,
20GB gp3) · a `t4g.nano` bastion with a freshly generated ED25519 key · all secrets in
SSM Parameter Store.

### Bootstrap

```bash
cd terraform
# secrets are already in terraform.tfvars (gitignored)
terraform init
terraform apply
```

Remote state (S3 + DynamoDB lock) is stubbed in `versions.tf`; uncomment it after
creating the bucket + lock table if you want shared state. Local state works until then.

### Database

The bot uses a local SQLite database (no separate DB server). `dbmate` applies the
migrations in `database/migrations/` on startup. Inspect it with the `sqlite3` CLI:

```bash
sqlite3 ./database/bunyanbot.sqlite3 ".tables"
```

## Secrets

`terraform/terraform.tfvars` holds the DB password and Discord credentials. It is
gitignored and pushed to SSM SecureString by terraform. Never commit it.
