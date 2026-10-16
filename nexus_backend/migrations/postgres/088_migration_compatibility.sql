-- compatibility: incremental
-- Expand-and-contract metadata: classify how far a migration may roll back.
-- Existing rows become 'breaking'; unmarked migration files default to it too.
ALTER TABLE accounting.schema_migrations
 ADD COLUMN IF NOT EXISTS compatibility TEXT NOT NULL DEFAULT 'breaking';
ALTER TABLE accounting.schema_migrations
 DROP CONSTRAINT IF EXISTS schema_migrations_compatibility_check;
ALTER TABLE accounting.schema_migrations
 ADD CONSTRAINT schema_migrations_compatibility_check
 CHECK(compatibility IN('incremental','breaking'));
