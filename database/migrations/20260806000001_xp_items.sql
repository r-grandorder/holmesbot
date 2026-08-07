-- migrate:up
-- XP-feeding items, held on the player balance row and spent via /ember to level any owned
-- servant: Ember of Wisdom (small; chat drops + shop) and Hellfire of Wisdom (big; war rewards
-- + mod grant).
ALTER TABLE grail_balance ADD COLUMN embers INTEGER NOT NULL DEFAULT 0;
ALTER TABLE grail_balance ADD COLUMN hellfire INTEGER NOT NULL DEFAULT 0;

-- migrate:down
-- (SQLite can't easily drop columns; leave in place)
