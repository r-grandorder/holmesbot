-- migrate:up
ALTER TABLE war ADD COLUMN ends_at INTEGER;      -- unix time the season auto-ends (NULL = manual)
ALTER TABLE war ADD COLUMN channel_id INTEGER;   -- where to post the auto-end result

-- migrate:down
ALTER TABLE war DROP COLUMN channel_id;
ALTER TABLE war DROP COLUMN ends_at;
