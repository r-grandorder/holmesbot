-- migrate:up
CREATE TABLE guild_config (
    guild_id              BIGINT PRIMARY KEY,
    enabled               BOOLEAN NOT NULL DEFAULT TRUE,
    allowed_channel_ids   BIGINT[] NOT NULL DEFAULT '{}',  -- empty = all channels
    staff_role_ids        BIGINT[] NOT NULL DEFAULT '{}',
    guess_servant_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    guess_shadow_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    guess_audio_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    cooldown_seconds      INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE scores (
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    points     BIGINT NOT NULL DEFAULT 0,
    wins       INTEGER NOT NULL DEFAULT 0,
    games      INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX scores_leaderboard_idx ON scores (guild_id, points DESC);

-- In-flight rounds live here, not in process memory, so they survive a deploy.
CREATE TABLE active_games (
    id            BIGSERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    channel_id    BIGINT NOT NULL,
    message_id    BIGINT,
    game_type     TEXT NOT NULL,
    servant_id    INTEGER NOT NULL,
    ascension     TEXT,
    answer_name   TEXT NOT NULL,
    points        INTEGER NOT NULL DEFAULT 0,
    wrong_guesses INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',
    started_by    BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX active_games_open_idx ON active_games (guild_id, status) WHERE status = 'active';

CREATE TABLE game_history (
    id             BIGSERIAL PRIMARY KEY,
    guild_id       BIGINT NOT NULL,
    channel_id     BIGINT NOT NULL,
    game_type      TEXT NOT NULL,
    servant_id     INTEGER NOT NULL,
    ascension      TEXT,
    winner_id      BIGINT,
    points_awarded INTEGER NOT NULL DEFAULT 0,
    outcome        TEXT NOT NULL,  -- win | timeout | revealed
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX game_history_guild_idx ON game_history (guild_id, created_at DESC);

-- Content-policy restrictions (global, not per-guild). Ships empty; staff curate.
CREATE TABLE restricted_servants (
    id             BIGSERIAL PRIMARY KEY,
    servant_id     INTEGER NOT NULL,
    scope          TEXT NOT NULL CHECK (scope IN ('full', 'ascension', 'costume')),
    ascension_keys TEXT[] NOT NULL DEFAULT '{}',  -- used when scope = 'ascension'/'costume'
    reason         TEXT,
    added_by       BIGINT,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX restricted_servants_servant_idx ON restricted_servants (servant_id);

-- Shared audit trail for bot + future dashboard actions.
CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT,
    actor_id   BIGINT,
    action     TEXT NOT NULL,
    detail     JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_guild_idx ON audit_log (guild_id, created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS restricted_servants;
DROP TABLE IF EXISTS game_history;
DROP TABLE IF EXISTS active_games;
DROP TABLE IF EXISTS scores;
DROP TABLE IF EXISTS guild_config;
