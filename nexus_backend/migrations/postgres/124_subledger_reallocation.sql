-- compatibility: incremental
-- R03/F07: a reversed allocation must be re-allocatable to the same open item
-- and voucher. History remains append-only; the old unique pair is removed.
ALTER TABLE accounting.subledger_allocations
 DROP CONSTRAINT IF EXISTS subledger_allocations_item_id_voucher_id_key;
