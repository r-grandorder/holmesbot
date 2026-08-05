-- migrate:up
-- Per-day reward counters for autobattle, split by mode so PvE (/ab fight) and PvP (/ab duel)
-- each get an independent daily cap, both separate from /duel's duel_daily table.
CREATE TABLE autobattle_daily (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  day      TEXT    NOT NULL,
  kind     TEXT    NOT NULL,       -- 'pve' | 'pvp'
  count    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild_id, user_id, day, kind)
);

-- migrate:down
DROP TABLE autobattle_daily;
