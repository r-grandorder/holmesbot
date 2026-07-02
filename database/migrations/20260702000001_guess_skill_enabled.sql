-- migrate:up
ALTER TABLE guild_config ADD COLUMN guess_skill_enabled INTEGER NOT NULL DEFAULT 1;

-- migrate:down
ALTER TABLE guild_config DROP COLUMN guess_skill_enabled;
