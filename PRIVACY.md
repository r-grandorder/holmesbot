# Holmes Bot — Privacy Policy

_Last updated: 2026-06-30_

Holmes Bot ("the bot") is a Fate/Grand Order guessing game for Discord. This policy
explains what data the bot processes, what it stores, and your choices. By adding or
using the bot you agree to this policy.

## Summary

- The bot does **not** store the content of your messages.
- It stores only Discord **user IDs** together with **game scores/stats**, plus
  server settings — the minimum needed to run the games and a leaderboard.
- Your data is **never sold, shared for advertising, or used to train AI/ML models.**

## What the bot processes

**Message content (not stored).** Gameplay works by typing a character's name in a
channel. While a round is active, the bot reads new messages in that channel in real
time only to:

1. check whether a message matches the round's answer (a guess), and
2. detect "hint" / "give up" requests when the bot is mentioned.

Message text is processed in memory and **discarded immediately**. It is never written
to a database, log, or any other storage, and is never sent to a third party. Server
admins can restrict the bot to specific game channels, so messages in other channels
are not processed.

## What the bot stores

The bot keeps a small self-hosted database containing:

- **Game scores** — your Discord user ID with your points, wins, and games played, per
  server (used for scoring and the leaderboard).
- **Round history** — per-round records: the game type, the correct answer, the
  winner's user ID, the outcome, and the channel/server IDs.
- **In-progress rounds** — temporary state for an active round (e.g. who started it),
  cleared when the round ends.
- **Server configuration** — server (guild) ID, which channels/games are enabled, and
  staff role IDs.
- **Moderation/audit records** — the user ID of a staff member who runs an admin
  action (e.g. restricting a character), and the action taken.

The bot does **not** collect message content, direct messages, email addresses, real
names, IP addresses, or any Discord profile data beyond the numeric user ID.

## How your data is used

Stored data is used solely to operate the bot: tracking scores, showing the
leaderboard, running and resolving rounds, applying server settings, and moderation.
It is **not** used for advertising, sold, or used to train machine-learning or AI
models.

## Who we share it with

We do not share your data with third parties for their own use. The bot relies on:

- **Discord** — the platform the bot runs on, governed by Discord's own Privacy Policy.
- **Atlas Academy** (`api.atlasacademy.io`) — the public Fate/Grand Order data source
  the bot fetches character art, voice clips, and metadata from. These are outbound
  requests for game assets only; **no user data is sent to Atlas Academy.**
- **The bot operator's hosting** — the database lives on a private server controlled by
  the operator; game image/audio assets are served from the operator's storage.

## Data retention and deletion

Scores and round history are retained so the leaderboard persists over time. You may
request deletion of your data at any time (see Contact); on request we remove the
records associated with your Discord user ID. Removing the bot from a server does not
automatically delete its stored data — request deletion if you want it removed.

## Children

The bot is intended for users who meet Discord's minimum age requirement (13+, or
higher where local law requires). It is not directed at children under that age.

## Changes to this policy

We may update this policy as the bot changes. Material changes will be reflected by
updating the "Last updated" date above in this document.

## Contact

For questions or a data-deletion request, open an issue on the bot's public
repository at <https://github.com/r-grandorder/holmesbot> or contact the moderators of
the server where you use the bot.
