-- migrate:up
ALTER TABLE war ADD COLUMN name TEXT;         -- optional war title, shown in /warstatus etc.
ALTER TABLE war ADD COLUMN description TEXT;  -- optional flavor/rules blurb

-- migrate:down
ALTER TABLE war DROP COLUMN description;
ALTER TABLE war DROP COLUMN name;
