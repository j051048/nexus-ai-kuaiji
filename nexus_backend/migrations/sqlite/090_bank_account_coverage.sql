-- compatibility: incremental
-- C01/F09: stable bank account master and per-account period coverage.
CREATE TABLE IF NOT EXISTS bank_accounts(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 name TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 active INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(book_id,name)
);
CREATE TABLE IF NOT EXISTS bank_statement_coverages(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES bank_accounts(id),
 book_id TEXT NOT NULL, period TEXT NOT NULL,
 opening_cents INTEGER NOT NULL, closing_cents INTEGER NOT NULL,
 source_ref TEXT NOT NULL, verified_by TEXT NOT NULL, verified_at TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(account_id,period)
);
ALTER TABLE bank_statement_lines ADD COLUMN bank_account_id TEXT REFERENCES bank_accounts(id);
