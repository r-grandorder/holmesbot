-- migrate:up
-- `points` becomes lifetime QP earned (leaderboard); `balance` is spendable QP
-- (moved by /pay). They start equal for existing players.
ALTER TABLE scores ADD COLUMN balance INTEGER NOT NULL DEFAULT 0;
UPDATE scores SET balance = points WHERE balance = 0;

-- migrate:down
ALTER TABLE scores DROP COLUMN balance;
