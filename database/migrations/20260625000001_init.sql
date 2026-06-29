-- migrate:up
CREATE TABLE guild_config (
    guild_id              INTEGER PRIMARY KEY,
    enabled               INTEGER NOT NULL DEFAULT 1,
    allowed_channel_ids   TEXT NOT NULL DEFAULT '[]',  -- JSON array; empty = all channels
    staff_role_ids        TEXT NOT NULL DEFAULT '[]',  -- JSON array
    guess_servant_enabled INTEGER NOT NULL DEFAULT 1,
    guess_shadow_enabled  INTEGER NOT NULL DEFAULT 1,
    guess_audio_enabled   INTEGER NOT NULL DEFAULT 1,
    cooldown_seconds      INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE scores (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    points     INTEGER NOT NULL DEFAULT 0,
    wins       INTEGER NOT NULL DEFAULT 0,
    games      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX scores_leaderboard_idx ON scores (guild_id, points DESC);

-- In-flight rounds live here, not in process memory, so they survive a deploy.
CREATE TABLE active_games (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    channel_id    INTEGER NOT NULL,
    message_id    INTEGER,
    game_type     TEXT NOT NULL,
    servant_id    INTEGER NOT NULL,
    ascension     TEXT,
    answer_name   TEXT NOT NULL,
    points        INTEGER NOT NULL DEFAULT 0,
    wrong_guesses INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',
    started_by    INTEGER,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at    TEXT NOT NULL
);
CREATE INDEX active_games_open_idx ON active_games (guild_id, status) WHERE status = 'active';

CREATE TABLE game_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL,
    channel_id     INTEGER NOT NULL,
    game_type      TEXT NOT NULL,
    servant_id     INTEGER NOT NULL,
    ascension      TEXT,
    winner_id      INTEGER,
    points_awarded INTEGER NOT NULL DEFAULT 0,
    outcome        TEXT NOT NULL,  -- win | timeout | revealed
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX game_history_guild_idx ON game_history (guild_id, created_at DESC);

-- Content-policy restrictions (global, not per-guild). Ships empty; staff curate.
CREATE TABLE restricted_servants (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    servant_id     INTEGER NOT NULL,
    scope          TEXT NOT NULL CHECK (scope IN ('full', 'ascension', 'costume')),
    ascension_keys TEXT NOT NULL DEFAULT '[]',  -- JSON array; used when scope = 'ascension'/'costume'
    reason         TEXT,
    added_by       INTEGER,
    added_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX restricted_servants_servant_idx ON restricted_servants (servant_id);

-- Shared audit trail for bot + future dashboard actions.
CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER,
    actor_id   INTEGER,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',  -- JSON object
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX audit_log_guild_idx ON audit_log (guild_id, created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS restricted_servants;
DROP TABLE IF EXISTS game_history;
DROP TABLE IF EXISTS active_games;
DROP TABLE IF EXISTS scores;
DROP TABLE IF EXISTS guild_config;
