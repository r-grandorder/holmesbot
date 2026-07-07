-- migrate:up
CREATE TABLE war (
  guild_id   INTEGER PRIMARY KEY,
  active     INTEGER NOT NULL DEFAULT 0,
  started_at TEXT
);
CREATE TABLE war_factions (
  guild_id INTEGER NOT NULL,
  slot     INTEGER NOT NULL,   -- 0..3
  name     TEXT NOT NULL,
  score    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild_id, slot)
);
CREATE TABLE war_members (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  slot     INTEGER NOT NULL,   -- which faction they joined this season
  score    INTEGER NOT NULL DEFAULT 0,   -- personal contribution this season
  PRIMARY KEY (guild_id, user_id)
);

-- migrate:down
DROP TABLE war_members;
DROP TABLE war_factions;
DROP TABLE war;
