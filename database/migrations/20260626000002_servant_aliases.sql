-- migrate:up
-- Admin-curated extra accepted names per servant (Atlas naming quirks, nicknames).
-- Global (an alias is valid in every guild), like restricted_servants.
CREATE TABLE servant_aliases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    servant_id INTEGER NOT NULL,
    alias      TEXT NOT NULL,   -- as typed, for display
    norm       TEXT NOT NULL,   -- normalized form, matched against guesses
    added_by   INTEGER,
    added_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX servant_aliases_servant_idx ON servant_aliases (servant_id);
CREATE UNIQUE INDEX servant_aliases_norm_idx ON servant_aliases (servant_id, norm);

-- migrate:down
DROP TABLE IF EXISTS servant_aliases;
