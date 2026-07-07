-- migrate:up
ALTER TABLE guild_config ADD COLUMN contract_announce_channel_id INTEGER;

-- migrate:down
ALTER TABLE guild_config DROP COLUMN contract_announce_channel_id;
