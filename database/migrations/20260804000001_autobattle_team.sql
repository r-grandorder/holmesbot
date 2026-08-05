-- migrate:up
-- A player's autobattle team: 1-3 of their owned servants, front-to-back by slot. Replace-all
-- semantics (set via /autobattleteam). One team per player; levels/kits come from the servant
-- data + servant_contracts at battle time, so only the servant ids are stored here.
CREATE TABLE autobattle_team (
  guild_id   INTEGER NOT NULL,
  user_id    INTEGER NOT NULL,
  slot       INTEGER NOT NULL,   -- 0..2, front to back
  servant_id INTEGER NOT NULL,
  PRIMARY KEY (guild_id, user_id, slot)
);

-- migrate:down
DROP TABLE autobattle_team;
