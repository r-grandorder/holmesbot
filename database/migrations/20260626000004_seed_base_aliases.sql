-- migrate:up
-- With version-specific matching, a short name only wins if it uniquely points to
-- one servant. Names whose canonical form carries extra tokens (Altria Pendragon,
-- Nero Claudius, ...) no longer accept the bare shortcut, so seed the common ones
-- to their BASE servant. IDs verified against the servant index. Staff tune via
-- /alias. (Many names -- EMIYA, Ishtar, Mordred, Scathach, Gilgamesh -- already
-- work via exact match and need no alias.)
INSERT INTO servant_aliases (servant_id, alias, norm) VALUES
  (100100, 'Altria', 'altria'),       -- Altria Pendragon (Saber)
  (100100, 'Artoria', 'artoria'),
  (900100, 'Jeanne', 'jeanne'),        -- Jeanne d'Arc (Ruler)
  (100500, 'Nero', 'nero'),            -- Nero Claudius (Saber)
  (500300, 'Tamamo', 'tamamo'),        -- Tamamo-no-Mae (Caster)
  (702300, 'Raikou', 'raikou'),        -- Minamoto-no-Raikou (Berserker)
  (702300, 'Minamoto', 'minamoto'),
  (700600, 'Kintoki', 'kintoki'),      -- Sakata Kintoki (Berserker)
  (300100, 'Cu', 'cu'),                -- Cu Chulainn (Lancer)
  (200200, 'Gil', 'gil'),              -- Gilgamesh (Archer)
  (401200, 'Ozy', 'ozy'),              -- Ozymandias
  (1100300, 'Jalter', 'jalter')        -- Jeanne d'Arc (Alter)
ON CONFLICT (servant_id, norm) DO NOTHING;

-- migrate:down
DELETE FROM servant_aliases
WHERE norm IN ('altria', 'artoria', 'jeanne', 'nero', 'tamamo', 'raikou',
               'minamoto', 'kintoki', 'cu', 'gil', 'ozy', 'jalter');
