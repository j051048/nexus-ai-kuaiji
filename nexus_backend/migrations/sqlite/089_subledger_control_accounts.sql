-- compatibility: incremental
-- C01/F04: explicit control-account configuration (SQLite).
CREATE TABLE IF NOT EXISTS subledger_control_accounts(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('receivable','payable','advance_received','advance_paid')),
 control_account_id TEXT NOT NULL, control_account_version INTEGER NOT NULL,
 required_aux_json TEXT NOT NULL DEFAULT '[]',
 effective_from TEXT NOT NULL, effective_to TEXT, active INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(ledger_id,kind,control_account_id,effective_from)
);
