-- migrate:up
ALTER TABLE guild_config RENAME COLUMN grail_drop_channel_ids TO event_channel_ids;

-- migrate:down
ALTER TABLE guild_config RENAME COLUMN event_channel_ids TO grail_drop_channel_ids;
