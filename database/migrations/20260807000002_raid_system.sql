-- migrate:up
-- Co-op autobattle raids: a shared-HP boss the server chips down with their /ab teams.
-- raid_defs are staff-authored templates (edited/enabled from the website, like kit_overrides);
-- raid_instances hold the live shared-HP state; raid_participation tracks per-user damage.
-- Ended instances + their participation are RETAINED (not deleted) to power raid history.

CREATE TABLE raid_defs (
  name        TEXT PRIMARY KEY,                 -- slug, e.g. "goetia_siege"
  def_json    TEXT NOT NULL,                    -- boss/HP/phases/rewards (validated before write)
  enabled     INTEGER NOT NULL DEFAULT 0,
  updated_by  INTEGER,
  updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Images uploaded from the dashboard (boss/phase art), served over the tunnel.
CREATE TABLE raid_sprites (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  image       BLOB NOT NULL,
  uploaded_by INTEGER,
  uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- At most one active row per guild (app-enforced in RaidService.start).
CREATE TABLE raid_instances (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id    INTEGER NOT NULL,
  def_name    TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  boss_id     INTEGER NOT NULL,                 -- servant id snapshot
  current_hp  INTEGER NOT NULL,
  total_hp    INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active',   -- active | defeated | expired
  last_hit_by INTEGER,
  channel_id  INTEGER,                          -- where to announce end (from /raidstart); null = silent
  started_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at  TEXT NOT NULL,
  ended_at    TEXT
);
CREATE INDEX raid_active_idx ON raid_instances (guild_id, status);

CREATE TABLE raid_participation (
  instance_id INTEGER NOT NULL,
  user_id     INTEGER NOT NULL,
  damage      INTEGER NOT NULL DEFAULT 0,
  attempts    INTEGER NOT NULL DEFAULT 0,
  rewarded    INTEGER NOT NULL DEFAULT 0,       -- idempotent payout guard
  last_at     TEXT,
  PRIMARY KEY (instance_id, user_id)
);
CREATE INDEX raid_participation_board_idx ON raid_participation (instance_id, damage DESC);

-- migrate:down
DROP TABLE IF EXISTS raid_participation;
DROP TABLE IF EXISTS raid_instances;
DROP TABLE IF EXISTS raid_sprites;
DROP TABLE IF EXISTS raid_defs;
