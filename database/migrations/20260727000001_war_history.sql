-- migrate:up
-- Archived wars, so /warhistory can show who fought on each faction in past seasons. The live
-- war/war_factions/war_members tables only ever hold the current season (start() wipes them);
-- on end -- and on an un-ended restart -- we snapshot the final rosters + scores into these.
CREATE TABLE war_history (
  id          INTEGER PRIMARY KEY,
  guild_id    INTEGER NOT NULL,
  name        TEXT,
  description TEXT,
  started_at  TEXT,
  ended_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  winner_slot INTEGER
);
CREATE INDEX war_history_guild_idx ON war_history (guild_id, id DESC);

CREATE TABLE war_history_factions (
  war_id INTEGER NOT NULL,
  slot   INTEGER NOT NULL,
  name   TEXT NOT NULL,
  score  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (war_id, slot)
);

CREATE TABLE war_history_members (
  war_id  INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  slot    INTEGER NOT NULL,
  score   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (war_id, user_id)
);

-- migrate:down
DROP TABLE war_history_members;
DROP TABLE war_history_factions;
DROP TABLE war_history;
