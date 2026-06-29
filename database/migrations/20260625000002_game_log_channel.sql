-- migrate:up
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS log_channel_id BIGINT;

-- migrate:down
ALTER TABLE guild_config DROP COLUMN IF EXISTS log_channel_id;
