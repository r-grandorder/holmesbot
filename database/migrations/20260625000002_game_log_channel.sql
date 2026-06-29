-- migrate:up
ALTER TABLE guild_config ADD COLUMN log_channel_id INTEGER;

-- migrate:down
ALTER TABLE guild_config DROP COLUMN log_channel_id;
