-- migrate:up
-- `points` becomes lifetime QP earned (leaderboard); `balance` is spendable QP
-- (moved by /pay). They start equal for existing players.
ALTER TABLE scores ADD COLUMN IF NOT EXISTS balance BIGINT NOT NULL DEFAULT 0;
UPDATE scores SET balance = points WHERE balance = 0;

-- migrate:down
ALTER TABLE scores DROP COLUMN IF EXISTS balance;
