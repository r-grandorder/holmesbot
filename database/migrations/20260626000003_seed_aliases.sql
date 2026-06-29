-- migrate:up
-- Seed common community nicknames into the alias table (replaces the old in-code
-- dict). Staff manage the rest via /alias.
INSERT INTO servant_aliases (servant_id, alias, norm) VALUES
  (504500, 'Castoria', 'castoria'),
  (101700, 'Musashi', 'musashi'),
  (102700, 'Okita', 'okita'),
  (2500100, 'Abby', 'abby'),
  (501900, 'Waver', 'waver'),
  (501900, 'Gramps', 'gramps')
ON CONFLICT (servant_id, norm) DO NOTHING;

-- migrate:down
DELETE FROM servant_aliases
WHERE norm IN ('castoria', 'musashi', 'okita', 'abby', 'waver', 'gramps');
