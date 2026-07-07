-- migrate:up
CREATE TABLE duel_daily (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  day      TEXT NOT NULL,
  rewarded INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild_id, user_id, day)
);

-- migrate:down
DROP TABLE duel_daily;
