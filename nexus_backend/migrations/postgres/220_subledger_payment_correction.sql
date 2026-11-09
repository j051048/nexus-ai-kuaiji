-- compatibility: incremental
-- Corrections remain immutable history, never fresh payment capacity.
CREATE OR REPLACE FUNCTION accounting.subledger_payment_current(payment TEXT) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.vouchers v JOIN accounting.books b ON b.id=v.book_id
  WHERE v.id=payment AND b.firm_id=accounting.actor_firm() AND accounting.book_allowed(b.id)
   AND v.reversal_of IS NULL
   AND NOT EXISTS(SELECT 1 FROM accounting.vouchers correction WHERE correction.reversal_of=v.id))
$$;
REVOKE ALL ON FUNCTION accounting.subledger_payment_current(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.subledger_payment_current(TEXT) TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.subledger_payment_correction_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NOT accounting.subledger_payment_current(NEW.voucher_id) THEN
  RAISE EXCEPTION 'Corrected payment requires reconciliation' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.subledger_payment_correction_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS subledger_payment_correction_guard ON accounting.subledger_allocations;
CREATE TRIGGER subledger_payment_correction_guard BEFORE INSERT ON accounting.subledger_allocations
 FOR EACH ROW EXECUTE FUNCTION accounting.subledger_payment_correction_guard();
