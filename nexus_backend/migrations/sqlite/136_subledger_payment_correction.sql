-- compatibility: incremental
-- Keep existing allocations and reversal evidence; reject only new invalid capacity.
CREATE TRIGGER IF NOT EXISTS subledger_payment_correction_guard BEFORE INSERT ON subledger_allocations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM vouchers v WHERE v.id=NEW.voucher_id
  AND v.reversal_of IS NULL AND NOT EXISTS(SELECT 1 FROM vouchers correction WHERE correction.reversal_of=v.id))
 THEN RAISE(ABORT,'Corrected payment requires reconciliation') END;
END;
