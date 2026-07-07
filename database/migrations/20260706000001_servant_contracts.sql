-- migrate:up
-- One row per (guild, user, servant): holds progress AND the active flag, so a
-- dismissed servant's progress persists and resumes if re-contracted. Exactly one row
-- per (guild, user) has active=1, enforced in the service layer (a transaction).
CREATE TABLE servant_contracts (
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    servant_id  INTEGER NOT NULL,
    level       INTEGER NOT NULL DEFAULT 1,
    xp          INTEGER NOT NULL DEFAULT 0,
    grails_used INTEGER NOT NULL DEFAULT 0,  -- cap = BASE_CAP + grails_used*5
    active      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id, servant_id)
);
CREATE INDEX servant_contracts_board_idx ON servant_contracts (guild_id, level DESC);
CREATE INDEX servant_contracts_active_idx ON servant_contracts (guild_id, user_id, active);

-- migrate:down
DROP TABLE IF EXISTS servant_contracts;
