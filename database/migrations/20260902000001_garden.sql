-- migrate:up
-- The garden: /water another player to grow their "height", /mulch them with Quality
-- Fertilizer to boost what they gain. Ported in spirit from the legacy bot's /water, minus
-- the skill trees and weather -- just a height that grows when someone waters you.

-- One row per (guild, user). height_mm is a float because early growth is sub-millimetre.
-- last_growth_at is the GROWTH cooldown and lives on the person being watered, not the
-- waterer: watering someone who grew recently still counts, it just doesn't grow them.
CREATE TABLE plant_heights (
  guild_id       INTEGER NOT NULL,
  user_id        INTEGER NOT NULL,
  height_mm      REAL    NOT NULL DEFAULT 1.0,   -- everyone starts as a 1mm sprout
  times_watered  INTEGER NOT NULL DEFAULT 0,
  last_growth_at TEXT,
  PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX plant_heights_tallest_idx ON plant_heights (guild_id, height_mm DESC);

-- The QP payout cooldown, on the WATERER. Separate from the growth cooldown above so the two
-- can be tuned independently: you may water freely, but only earn QP twice a day.
CREATE TABLE water_rewards (
  guild_id       INTEGER NOT NULL,
  user_id        INTEGER NOT NULL,
  last_reward_at TEXT    NOT NULL,
  PRIMARY KEY (guild_id, user_id)
);

-- An active Quality Fertilizer on a user, applied by someone else via /mulch. One row per
-- target; re-mulching refreshes it rather than stacking, so the buff can't be piled up.
CREATE TABLE mulch_effects (
  guild_id   INTEGER NOT NULL,
  user_id    INTEGER NOT NULL,   -- who is mulched (the growth target)
  multiplier REAL    NOT NULL,
  expires_at TEXT    NOT NULL,
  applied_by INTEGER,
  PRIMARY KEY (guild_id, user_id)
);

-- Quality Fertilizer stock rides alongside the other spendable items.
ALTER TABLE grail_balance ADD COLUMN fertilizer INTEGER NOT NULL DEFAULT 0;

-- migrate:down
DROP TABLE IF EXISTS mulch_effects;
DROP TABLE IF EXISTS water_rewards;
DROP TABLE IF EXISTS plant_heights;
