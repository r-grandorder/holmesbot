-- migrate:up
ALTER TABLE war ADD COLUMN banner BLOB;

-- migrate:down
ALTER TABLE war DROP COLUMN banner;
