-- compatibility: incremental
-- C01/F04: explicit control-account configuration. Account codes are no longer
-- the source of truth; legacy unconfigured items remain readable for history.
CREATE TABLE IF NOT EXISTS accounting.subledger_control_accounts(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),
 kind TEXT NOT NULL CHECK(kind IN('receivable','payable','advance_received','advance_paid')),
 control_account_id TEXT NOT NULL,
 control_account_version INTEGER NOT NULL,
 required_aux_json TEXT NOT NULL DEFAULT '[]',
 effective_from TEXT NOT NULL, effective_to TEXT,
 active BOOLEAN NOT NULL DEFAULT true,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(ledger_id,kind,control_account_id,effective_from)
);
ALTER TABLE accounting.subledger_control_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.subledger_control_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY subledger_control_read ON accounting.subledger_control_accounts
 FOR SELECT TO accounting_app USING(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p
  WHERE p.ledger_id=subledger_control_accounts.ledger_id AND accounting.book_allowed(p.book_id)));
CREATE POLICY subledger_control_write ON accounting.subledger_control_accounts
 FOR ALL TO accounting_app USING(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p
  WHERE p.ledger_id=subledger_control_accounts.ledger_id AND accounting.book_allowed(p.book_id,true)))
 WITH CHECK(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p
  WHERE p.ledger_id=subledger_control_accounts.ledger_id AND accounting.book_allowed(p.book_id,true)));
CREATE TRIGGER p0_control_dependency_revision AFTER INSERT OR UPDATE OR DELETE
 ON accounting.subledger_control_accounts FOR EACH ROW
 EXECUTE FUNCTION accounting.close_dependency_bump('ledger');
