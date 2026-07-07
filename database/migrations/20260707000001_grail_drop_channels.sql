-- migrate:up
ALTER TABLE guild_config ADD COLUMN grail_drop_channel_ids TEXT NOT NULL DEFAULT '[]';

-- migrate:down
ALTER TABLE guild_config DROP COLUMN grail_drop_channel_ids;
