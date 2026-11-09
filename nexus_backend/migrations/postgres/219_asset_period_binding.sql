-- compatibility: incremental
-- A persistent card may serve later periods of the same ledger, never another tenant.
CREATE UNIQUE INDEX IF NOT EXISTS periodic_asset_period_once
 ON accounting.periodic_runs(asset_id,period) WHERE asset_id IS NOT NULL AND status<>'void';
CREATE OR REPLACE FUNCTION accounting.asset_history_allowed(asset TEXT,target_book TEXT) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.periodic_assets a JOIN accounting.ledger_periods origin ON origin.book_id=a.book_id
  JOIN accounting.ledger_periods target ON target.book_id=target_book
  WHERE a.id=asset AND origin.ledger_id=target.ledger_id AND origin.firm_id=accounting.actor_firm()
   AND accounting.book_allowed(a.book_id) AND accounting.book_allowed(target_book)
   AND NOT EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=target.ledger_id
    AND p.period<=target.period AND NOT accounting.book_allowed(p.book_id)))
$$;
REVOKE ALL ON FUNCTION accounting.asset_history_allowed(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.asset_history_allowed(TEXT,TEXT) TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.periodic_asset_period_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP='UPDATE' AND (NEW.asset_id,NEW.book_id,NEW.period,NEW.kind,NEW.input_fingerprint)
   IS DISTINCT FROM (OLD.asset_id,OLD.book_id,OLD.period,OLD.kind,OLD.input_fingerprint) THEN
  RAISE EXCEPTION 'Periodic asset run identity is immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.asset_id IS NOT NULL THEN
  IF NOT accounting.book_review_allowed(NEW.book_id) OR NOT accounting.asset_history_allowed(NEW.asset_id,NEW.book_id) OR NOT EXISTS(
   SELECT 1 FROM accounting.periodic_assets a
   JOIN accounting.ledger_periods origin ON origin.book_id=a.book_id
   JOIN accounting.ledger_periods target ON target.book_id=NEW.book_id
   WHERE a.id=NEW.asset_id AND a.kind=NEW.kind AND a.status='active'
    AND origin.ledger_id=target.ledger_id AND origin.company_id=target.company_id
    AND origin.firm_id=target.firm_id AND origin.period<=target.period AND target.period=NEW.period
    AND accounting.book_allowed(a.book_id)
  ) THEN RAISE EXCEPTION 'Invalid asset period or review scope' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM accounting.periodic_runs r WHERE r.asset_id=NEW.asset_id
    AND r.period<>NEW.period AND r.status<>'void' AND (r.period>NEW.period OR r.status<>'posted')) THEN
   RAISE EXCEPTION 'Unfinished or later asset period exists' USING ERRCODE='23514';
  END IF;
  IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM accounting.periodic_runs r JOIN accounting.vouchers v
    ON v.reversal_of=r.voucher_id WHERE r.asset_id=NEW.asset_id AND r.status='posted') THEN
   RAISE EXCEPTION 'Corrected asset history requires reconciliation' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.periodic_asset_period_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS periodic_asset_period_guard ON accounting.periodic_runs;
CREATE TRIGGER periodic_asset_period_guard BEFORE INSERT OR UPDATE ON accounting.periodic_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.periodic_asset_period_guard();
