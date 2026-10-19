-- compatibility: incremental
-- C01/R1: explicit ledger-level applicability, item auxiliary facts, bank
-- account -> ledger account mapping, and minimal grants for the new tables.
CREATE TABLE IF NOT EXISTS accounting.subledger_control_settings(
 ledger_id TEXT PRIMARY KEY REFERENCES accounting.ledgers(id),
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 status TEXT NOT NULL CHECK(status IN('pending','configured','not_applicable')),
 reason TEXT NOT NULL DEFAULT '', confirmed_by TEXT, confirmed_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
ALTER TABLE accounting.subledger_items ADD COLUMN IF NOT EXISTS aux_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE accounting.bank_accounts ADD COLUMN IF NOT EXISTS ledger_account_id TEXT;
ALTER TABLE accounting.bank_accounts ADD COLUMN IF NOT EXISTS ledger_account_version INTEGER;
ALTER TABLE accounting.bank_accounts ADD COLUMN IF NOT EXISTS aux_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE accounting.subledger_control_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.subledger_control_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY subledger_control_settings_read ON accounting.subledger_control_settings
 FOR SELECT TO accounting_app USING(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=subledger_control_settings.ledger_id
  AND accounting.book_allowed(p.book_id)));
CREATE POLICY subledger_control_settings_write ON accounting.subledger_control_settings
 FOR ALL TO accounting_app USING(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=subledger_control_settings.ledger_id
  AND accounting.book_allowed(p.book_id,true)))
 WITH CHECK(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=subledger_control_settings.ledger_id
  AND accounting.book_allowed(p.book_id,true)));

GRANT SELECT,INSERT,UPDATE ON accounting.subledger_control_accounts TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.subledger_control_settings TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.bank_accounts TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.bank_statement_coverages TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.bank_statement_lines TO accounting_app;

CREATE TRIGGER p0_control_settings_dependency_revision AFTER INSERT OR UPDATE OR DELETE
 ON accounting.subledger_control_settings FOR EACH ROW
 EXECUTE FUNCTION accounting.close_dependency_bump('ledger');
