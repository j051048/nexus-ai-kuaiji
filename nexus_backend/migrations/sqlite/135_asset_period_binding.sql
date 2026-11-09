-- compatibility: incremental
CREATE UNIQUE INDEX IF NOT EXISTS periodic_asset_period_once
 ON periodic_runs(asset_id,period) WHERE asset_id IS NOT NULL AND status<>'void';
CREATE TRIGGER IF NOT EXISTS periodic_asset_period_insert BEFORE INSERT ON periodic_runs WHEN NEW.asset_id IS NOT NULL BEGIN
 SELECT CASE WHEN NOT EXISTS(
  SELECT 1 FROM periodic_assets a JOIN ledger_periods origin ON origin.book_id=a.book_id
  JOIN ledger_periods target ON target.book_id=NEW.book_id WHERE a.id=NEW.asset_id
  AND a.kind=NEW.kind AND a.status='active' AND origin.ledger_id=target.ledger_id
  AND origin.company_id=target.company_id AND origin.firm_id=target.firm_id
  AND origin.period<=target.period AND target.period=NEW.period
 ) THEN RAISE(ABORT,'Invalid asset period scope') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM periodic_runs r WHERE r.asset_id=NEW.asset_id
  AND r.period<>NEW.period AND r.status<>'void' AND (r.period>NEW.period OR r.status<>'posted'))
 THEN RAISE(ABORT,'Unfinished or later asset period exists') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM periodic_runs r JOIN vouchers v ON v.reversal_of=r.voucher_id
  WHERE r.asset_id=NEW.asset_id AND r.status='posted')
 THEN RAISE(ABORT,'Corrected asset history requires reconciliation') END;
END;
CREATE TRIGGER IF NOT EXISTS periodic_asset_period_update BEFORE UPDATE ON periodic_runs BEGIN
 SELECT CASE WHEN NEW.asset_id IS NOT OLD.asset_id OR NEW.book_id<>OLD.book_id OR NEW.period<>OLD.period
  OR NEW.kind<>OLD.kind OR NEW.input_fingerprint<>OLD.input_fingerprint
 THEN RAISE(ABORT,'Periodic asset run identity is immutable') END;
END;
