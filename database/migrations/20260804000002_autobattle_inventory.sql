-- migrate:up
-- Items a player owns for autobattle (bought with QP in /ab shop). Consumable: a copy is spent
-- only when its effect actually fires in a battle. quantity 0 rows are deleted, not kept.
CREATE TABLE autobattle_inventory (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  item_id  TEXT    NOT NULL,   -- key into data/autobattle_items.json
  quantity INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild_id, user_id, item_id)
);

-- migrate:down
DROP TABLE autobattle_inventory;
