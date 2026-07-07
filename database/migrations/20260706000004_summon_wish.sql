-- migrate:up
ALTER TABLE grail_balance ADD COLUMN wish_servant_id INTEGER;

-- migrate:down
ALTER TABLE grail_balance DROP COLUMN wish_servant_id;
