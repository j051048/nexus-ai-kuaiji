-- compatibility: incremental
-- A balanced extra pair is still not the difference that was confirmed.
CREATE VIEW IF NOT EXISTS asset_reconciliation_differences AS
SELECT r.id AS run_id,
 CASE WHEN (CAST(substr(r.period,1,4) AS INTEGER)-CAST(substr(json_extract(r.reconciliation_json,'$.start_period'),1,4) AS INTEGER))*12
 +CAST(substr(r.period,6,2) AS INTEGER)-CAST(substr(json_extract(r.reconciliation_json,'$.start_period'),6,2) AS INTEGER)+1
 >=json_extract(r.reconciliation_json,'$.life_months')
 THEN json_extract(r.reconciliation_json,'$.cost_cents')-json_extract(r.reconciliation_json,'$.residual_cents')
 ELSE (json_extract(r.reconciliation_json,'$.cost_cents')-json_extract(r.reconciliation_json,'$.residual_cents'))/json_extract(r.reconciliation_json,'$.life_months')*
 ((CAST(substr(r.period,1,4) AS INTEGER)-CAST(substr(json_extract(r.reconciliation_json,'$.start_period'),1,4) AS INTEGER))*12
 +CAST(substr(r.period,6,2) AS INTEGER)-CAST(substr(json_extract(r.reconciliation_json,'$.start_period'),6,2) AS INTEGER)+1)
 END-json_extract(r.reconciliation_json,'$.net_posted_cents') AS expected_cents
FROM periodic_runs r WHERE r.reconciliation_json IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_lines_review BEFORE UPDATE ON periodic_drafts
WHEN NEW.status IN('approved','posted') AND EXISTS(SELECT 1 FROM periodic_runs WHERE id=NEW.run_id AND reconciliation_json IS NOT NULL)
BEGIN
 SELECT CASE WHEN (SELECT COUNT(*) FROM periodic_draft_lines WHERE draft_id=NEW.id)<>2
 OR NOT EXISTS(SELECT 1 FROM asset_reconciliation_differences WHERE run_id=NEW.run_id AND expected_cents>0)
 OR EXISTS(SELECT 1 FROM periodic_draft_lines l JOIN periodic_runs r ON r.id=NEW.run_id
 JOIN asset_reconciliation_differences d ON d.run_id=r.id WHERE l.draft_id=NEW.id AND NOT(
 json(l.aux_json)='{}' AND ((l.line_no=1 AND l.account_id=json_extract(r.reconciliation_json,'$.expense_account_id') AND l.account_version=json_extract(r.reconciliation_json,'$.expense_version') AND l.debit_cents=d.expected_cents AND l.credit_cents=0)
 OR (l.line_no=2 AND l.account_id=json_extract(r.reconciliation_json,'$.accum_account_id') AND l.account_version=json_extract(r.reconciliation_json,'$.accum_version') AND l.credit_cents=d.expected_cents AND l.debit_cents=0))))
 THEN RAISE(ABORT,'Reconciliation lines must match reviewed difference') END;
END;
