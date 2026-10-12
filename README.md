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

## Halloween testing

Halloween ships off. Set `HALLOWEEN_WHITELIST` to comma-separated Discord user IDs to
enable it for testers, or `all` to enable it for everyone with contract access. Players
must also be admitted by `CONTRACT_WHITELIST`; an empty Halloween whitelist loads no
Halloween commands and leaves ordinary drops alone. Restart the bot after changing env vars.

```dotenv
CONTRACT_WHITELIST=123456789012345678
HALLOWEEN_WHITELIST=123456789012345678
HALLOWEEN_RANDOM_EVENTS=true
HALLOWEEN_START_AT=
HALLOWEEN_END_AT=
```

For manual testing only, set `HALLOWEEN_RANDOM_EVENTS=false` and restart the bot.
Whitelisted moderators can still use `/halloweenevent`, players can claim those events,
and `/candy` still works. Ordinary ember/grail drops and `/triggerevent` continue normally.
Whitelists and event dates still apply. The setting defaults to `true` when unset.

Use `/summonconfig eventchannel` to choose the automatic drop channels. During the active
window, Halloween fully replaces ordinary ember/grail events when `HALLOWEEN_RANDOM_EVENTS=true`.
Eligible players' single drops become
**Trick or Treat** (each player chooses once during the two-minute window: Treat for 1 guaranteed
candy, or Trick for a 50% chance of 2 or 4 candy and a 50% chance of no candy plus a QP penalty)
or **Candy Spill** (1 candy per player, plus 1 extra each if
everyone collects their piece). Drop chances and cooldowns are unchanged; grail replacements
still award one grail per participant.
Trick's four outcomes are equally likely: Liz charges 1% of your current QP for admission
(up to 5,000), Mecha Eli-chan charges 2% for repairs (up to 10,000), Liz gives 2 candy,
or Carmilla gives 4 candy. Trick or Treat stays open until its deadline with no participant
cap; each player's outcome is independent. Its public embed shows the latest outcome,
the five most recent results, and a running participation count. The private result reports
the actual candy received and QP lost. Percentage losses round down to whole QP,
with no minimum charge. Penalties use only available spendable QP, never create debt, and leave lifetime scores
and existing candy/grails unchanged. Claims and QP penalties commit together, once per player per event.
Liz uses the emotion portraits bundled in `assets/halloween/liz/`: worried/upset for
spilled candy, embarrassed for the Carmilla line and candy pickups, happy for treats,
encores and a completed spill, and pout for admission fees. Opening lines keep their
matching expression throughout the event. Carmilla and Mecha Eli-chan use their own
permitted Atlas faces; the latest Trick or Treat result updates the public thumbnail and
removes Liz's attachment. Missing local images fall back to Liz's permitted Atlas face.
QP encounters continue normally. With random Halloween events enabled, unlisted players cannot
trigger ordinary ember/grail events during the window. Only whitelisted players can trigger
and claim Halloween events. Testers' event messages are visible in the channel. A whitelisted moderator can
use `/halloweenevent` to spawn either event in the current channel or the optional `channel`
without waiting for a roll. Manual triggers work outside the automatic drop channels,
wherever the bot can post; the whitelist and active dates still apply.
Manual triggers can run alongside other Halloween events in the same channel. Each event
allows one claim per player and ends independently. Automatic Halloween drops skip channels
with an active Halloween event instead of falling back to ordinary events. These rolls still
consume their usual drop cooldown. With random Halloween events enabled, `/triggerevent`
blocks ordinary ember/grail spawns during the window and directs moderators to
`/halloweenevent`; its QP option remains available.

`/candy [user] [servant] [quantity]` spends your candy to feed one of your servants, or
another player's servant when `user` is selected. It defaults to the recipient's active
servant; the servant picker shows their roster. Both players need Halloween access.
Each candy grants 3,000 XP, equal to one Hellfire or six Embers of Wisdom. Feeding your
own servant stays private. Gifts show a public embed naming the giver and recipient,
with the servant's face thumbnail and an Atlas level-up subtitle, with no audio.
Halloween Liz uses her happy portrait when fed candy.
Errors and the giver's remaining candy balance stay private. For example,
`/candy user:@Alex quantity:1` feeds Alex's active servant using one of your candies.
Missing dialogue falls back to a descriptive response;
this includes the current JP-only roster, whose Atlas export has no subtitles. Art
restrictions still apply. `/items` includes the candy balance. Feeding keeps surplus
candy when the servant reaches its cap. Claims, group bonuses, and XP spending are transactional.

Optional `HALLOWEEN_START_AT` and `HALLOWEEN_END_AT` accept ISO timestamps with a timezone.
At the end, ordinary drops resume automatically. Leave the whitelist enabled so collected
candy remains usable; clearing it disables the commands too, without deleting balances.
New events last two minutes from when their message is posted, and end sooner if the
seasonal end time arrives. Expired messages are deleted immediately; completed Candy Spills
show their result for 15 seconds before deletion. Cleanup retries transient failures.
In-progress event buttons expire on bot restart, as ordinary drop buttons do; awarded candy
is persistent. The container's normal Atlas sync bakes in the dialogue. For local development,
run `make sync-data` to refresh the NA and JP indexes after updating the sync script.

Run the focused checks with `python -m unittest discover -s tests -v` using Python 3.12
and the dependencies from `requirements.txt`.

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
