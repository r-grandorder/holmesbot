-- migrate:up
-- Live, web-editable kit overrides (mod-only). The bot loads the baked JSON kits at boot then
-- layers these rows on top, and re-applies them on each edit, so a change takes effect within
-- seconds without an image rebuild. The git data/kits/*.json files remain the seed/archive.
CREATE TABLE kit_overrides (
  servant_id INTEGER PRIMARY KEY,
  kit_json   TEXT NOT NULL,                       -- full kit dict as JSON, validated before write
  updated_by INTEGER,                             -- Discord user id of the editor
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- migrate:down
DROP TABLE IF EXISTS kit_overrides;
