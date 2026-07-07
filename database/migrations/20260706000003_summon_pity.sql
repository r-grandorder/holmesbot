-- migrate:up
-- Per-user summon pity: rolls since the last 5-star (a guaranteed 5-star is forced at
-- contract_game.PITY_5STAR). Stored on grail_balance, the per-user contract-state row.
ALTER TABLE grail_balance ADD COLUMN pity_rolls INTEGER NOT NULL DEFAULT 0;

-- migrate:down
ALTER TABLE grail_balance DROP COLUMN pity_rolls;
