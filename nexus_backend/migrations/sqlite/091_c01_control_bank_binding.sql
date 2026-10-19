-- compatibility: incremental
-- C01/R1: applicability, item auxiliary facts and bank account mapping.
CREATE TABLE IF NOT EXISTS subledger_control_settings(
 ledger_id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('pending','configured','not_applicable')),
 reason TEXT NOT NULL DEFAULT '', confirmed_by TEXT, confirmed_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
ALTER TABLE subledger_items ADD COLUMN aux_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE bank_accounts ADD COLUMN ledger_account_id TEXT;
ALTER TABLE bank_accounts ADD COLUMN ledger_account_version INTEGER;
ALTER TABLE bank_accounts ADD COLUMN aux_json TEXT NOT NULL DEFAULT '{}';
