-- compatibility: incremental
-- UPG Batch 01: 307 enables RLS on the concurrently added relation table but
-- does not force it for the table owner role. Keep the existing policies.
ALTER TABLE accounting.fee_source_relations FORCE ROW LEVEL SECURITY;
