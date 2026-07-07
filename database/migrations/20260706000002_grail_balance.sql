-- migrate:up
-- Spendable grails earned from random chat drops; spent to raise a servant's level cap.
CREATE TABLE grail_balance (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    balance  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

-- migrate:down
DROP TABLE IF EXISTS grail_balance;
