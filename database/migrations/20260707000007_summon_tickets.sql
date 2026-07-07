-- migrate:up
ALTER TABLE grail_balance ADD COLUMN summon_tickets INTEGER NOT NULL DEFAULT 0;

-- migrate:down
ALTER TABLE grail_balance DROP COLUMN summon_tickets;
