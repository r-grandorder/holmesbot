-- migrate:up
ALTER TABLE grail_balance ADD COLUMN candy INTEGER NOT NULL DEFAULT 0 CHECK (candy >= 0);

-- Rewards and the group completion bonus commit with the claim, even if Discord fails.
CREATE TABLE halloween_events (
    id TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('trick_or_treat', 'candy_spill')),
    slots INTEGER NOT NULL CHECK (slots BETWEEN 1 AND 8),
    grails INTEGER NOT NULL DEFAULT 0 CHECK (grails IN (0, 1)),
    expires_at REAL NOT NULL
);
CREATE INDEX halloween_events_expiry_idx ON halloween_events (expires_at);
CREATE TABLE halloween_claims (
    event_id TEXT NOT NULL REFERENCES halloween_events (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    candy INTEGER NOT NULL,
    PRIMARY KEY (event_id, user_id),
    UNIQUE (event_id, slot)
);

-- migrate:down
DROP TABLE IF EXISTS halloween_claims;
DROP TABLE IF EXISTS halloween_events;
-- Retain the candy balance, following the existing XP-item migrations.
