-- compatibility: incremental
-- Expand-and-contract metadata: classify how far a migration may roll back.
-- Existing rows become 'breaking'; unmarked migration files default to it too.
ALTER TABLE schema_migrations ADD COLUMN compatibility TEXT NOT NULL DEFAULT 'breaking';
