-- OPT04-D: one effective original posting per logical draft/import source.
-- Reversal uniqueness is already enforced by voucher_relations. SQLite aborts
-- this migration if historical duplicates exist; it never deletes or rewrites.
CREATE UNIQUE INDEX IF NOT EXISTS uq_voucher_sources_logical
 ON voucher_sources(source_kind,source_id)
 WHERE source_id IS NOT NULL AND source_kind IN('draft','source');
