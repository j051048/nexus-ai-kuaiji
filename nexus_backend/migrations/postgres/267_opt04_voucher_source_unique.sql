-- OPT04-D: a logical draft/import source may create at most one original
-- voucher. Reversal uniqueness is already enforced by voucher_relations;
-- manual/adjustment rows do not carry a logical source id. Historical rows are
-- checked before the index is created; this migration never deletes or rewrites
-- postings.
DO $$
DECLARE duplicate RECORD;
BEGIN
 SELECT source_kind,source_id,count(*) AS n INTO duplicate
 FROM accounting.voucher_sources
 WHERE source_id IS NOT NULL AND source_kind IN('draft','source')
 GROUP BY source_kind,source_id HAVING count(*)>1 LIMIT 1;
 IF FOUND THEN
  RAISE EXCEPTION 'voucher_sources duplicate source %, %, % rows',
   duplicate.source_kind, duplicate.source_id, duplicate.n USING ERRCODE='23505';
 END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_voucher_sources_logical
 ON accounting.voucher_sources(source_kind,source_id)
 WHERE source_id IS NOT NULL AND source_kind IN('draft','source');
