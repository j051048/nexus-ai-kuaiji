-- compatibility: incremental
ALTER TABLE periodic_runs ADD COLUMN reconciliation_json TEXT;
CREATE VIEW IF NOT EXISTS asset_reconciliation_sources AS
WITH RECURSIVE targets AS (
 SELECT a.*, t.book_id AS target_book_id,t.period AS target_period,
 e.current_version AS expense_version,c.current_version AS accum_version
 FROM periodic_assets a JOIN ledger_periods o ON o.book_id=a.book_id
 JOIN ledger_periods t ON t.ledger_id=o.ledger_id AND t.company_id=o.company_id AND t.period>=o.period
 JOIN ledger_accounts e ON e.id=a.expense_account_id JOIN ledger_accounts c ON c.id=a.accum_account_id
), history(asset_id,target_book_id,voucher_id) AS (
 SELECT t.id,t.target_book_id,r.voucher_id FROM targets t JOIN periodic_runs r ON r.asset_id=t.id
 WHERE r.status='posted' AND r.period<t.target_period AND r.voucher_id IS NOT NULL
 UNION
 SELECT h.asset_id,h.target_book_id,v.id FROM history h JOIN vouchers v ON v.reversal_of=h.voucher_id
), amounts AS (
 SELECT h.asset_id,h.target_book_id,h.voucher_id,v.period,v.book_id,v.content_fingerprint,
 COALESCE(SUM(CASE WHEN l.account_id=t.expense_account_id THEN l.debit_cents-l.credit_cents ELSE 0 END),0) AS net,
 COALESCE(SUM(CASE WHEN l.account_id=t.accum_account_id THEN l.credit_cents-l.debit_cents ELSE 0 END),0) AS accum,
 SUM(CASE WHEN l.account_id NOT IN(t.expense_account_id,t.accum_account_id) OR l.aux_json<>'{}' THEN 1 ELSE 0 END) AS unsupported
 FROM history h JOIN targets t ON t.id=h.asset_id AND t.target_book_id=h.target_book_id
 JOIN vouchers v ON v.id=h.voucher_id JOIN voucher_lines l ON l.voucher_id=v.id
 GROUP BY h.asset_id,h.target_book_id,h.voucher_id,v.period,v.book_id,v.content_fingerprint
)
SELECT t.id AS asset_id,t.target_book_id AS book_id,json_object('asset_id',t.id,'book_id',t.target_book_id,'period',t.target_period,'cost_cents',t.cost_cents,'residual_cents',t.residual_cents,'life_months',t.life_months,'start_period',t.start_period,'expense_account_id',t.expense_account_id,'expense_version',t.expense_version,'accum_account_id',t.accum_account_id,'accum_version',t.accum_version,'net_posted_cents',COALESCE((SELECT SUM(net) FROM amounts m WHERE m.asset_id=t.id AND m.target_book_id=t.target_book_id),0),'history',json(COALESCE((SELECT json_group_array(json_object('id',m.voucher_id,'period',m.period,'book_id',m.book_id,'fingerprint',m.content_fingerprint,'net_cents',m.net,'accum_cents',m.accum,'unsupported',m.unsupported)) FROM (SELECT * FROM amounts ORDER BY voucher_id) m WHERE m.asset_id=t.id AND m.target_book_id=t.target_book_id),'[]'))) AS source_json FROM targets t;
DROP TRIGGER IF EXISTS periodic_asset_period_insert;
CREATE TRIGGER periodic_asset_period_insert BEFORE INSERT ON periodic_runs WHEN NEW.asset_id IS NOT NULL BEGIN
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
 SELECT CASE WHEN NEW.reconciliation_json IS NULL AND EXISTS(SELECT 1 FROM periodic_runs r JOIN vouchers v ON v.reversal_of=r.voucher_id
  WHERE r.asset_id=NEW.asset_id AND r.status='posted')
 THEN RAISE(ABORT,'Corrected asset history requires reconciliation') END;
END;
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_source_insert BEFORE INSERT ON periodic_runs WHEN NEW.reconciliation_json IS NOT NULL BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM asset_reconciliation_sources s WHERE s.asset_id=NEW.asset_id AND s.book_id=NEW.book_id
 AND json(s.source_json)=json(NEW.reconciliation_json)) THEN RAISE(ABORT,'Reconciliation must match current scoped source') END;
END;
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_source_update BEFORE UPDATE ON periodic_runs BEGIN
 SELECT CASE WHEN NEW.reconciliation_json IS NOT OLD.reconciliation_json OR OLD.status IN('void','posted') AND NEW.status<>OLD.status
 THEN RAISE(ABORT,'Reconciliation source and terminal run are immutable') END;
END;
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_review BEFORE UPDATE ON periodic_drafts BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM periodic_runs r WHERE r.id=NEW.run_id AND r.status='void')
 THEN RAISE(ABORT,'Reconciliation replaced this draft') END;
 SELECT CASE WHEN NEW.status IN('approved','posted') AND EXISTS(SELECT 1 FROM periodic_runs r WHERE r.id=NEW.run_id AND r.reconciliation_json IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM asset_reconciliation_sources s WHERE s.asset_id=r.asset_id AND s.book_id=r.book_id AND json(s.source_json)=json(r.reconciliation_json)))
 THEN RAISE(ABORT,'Reconciliation source changed') END;
END;
