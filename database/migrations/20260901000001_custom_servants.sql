-- migrate:up
-- Live, web-editable custom summonable servants (mod-only). Mirrors kit_overrides: the bot
-- loads the baked data/custom_servants.json at boot, then layers these rows on top and
-- re-applies them on each edit, so a change takes effect within seconds without an image
-- rebuild. The git JSON file remains the seed/archive.
--
-- id is the servant id and is always NEGATIVE: ServantIndex.load lets a custom entry's id
-- win over any earlier dup, so a positive id here would silently shadow a real Atlas servant.
-- Enforced in validate_custom_servant and by the CHECK below.
--
-- enabled ships a unit dark: a half-built servant stays out of the summon pool until a mod
-- flips it on (same pattern as raid_defs.enabled).
CREATE TABLE custom_servants (
  id         INTEGER PRIMARY KEY CHECK (id < 0),
  def_json   TEXT NOT NULL,                     -- full servant dict as JSON, validated before write
  enabled    INTEGER NOT NULL DEFAULT 0,
  updated_by INTEGER,                           -- Discord user id of the editor
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- migrate:down
DROP TABLE IF EXISTS custom_servants;
