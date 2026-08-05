-- migrate:up
-- Which item a player has equipped to each of their servants for autobattle. Keyed by servant
-- (not team slot) so equips survive team edits/reorders. One item per servant; the app also
-- enforces one of each item type across the fielded team. Cleared when a copy is consumed and
-- the player has none left.
CREATE TABLE autobattle_equip (
  guild_id   INTEGER NOT NULL,
  user_id    INTEGER NOT NULL,
  servant_id INTEGER NOT NULL,
  item_id    TEXT    NOT NULL,
  PRIMARY KEY (guild_id, user_id, servant_id)
);

-- migrate:down
DROP TABLE autobattle_equip;
