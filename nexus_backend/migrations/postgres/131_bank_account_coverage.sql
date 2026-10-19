-- compatibility: incremental
-- C01/F09: stable bank account master and per-account period coverage.
CREATE TABLE IF NOT EXISTS accounting.bank_accounts(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 name TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 active BOOLEAN NOT NULL DEFAULT true,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(book_id,name)
);
CREATE TABLE IF NOT EXISTS accounting.bank_statement_coverages(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounting.bank_accounts(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL,
 opening_cents BIGINT NOT NULL, closing_cents BIGINT NOT NULL,
 source_ref TEXT NOT NULL, verified_by TEXT NOT NULL REFERENCES accounting.users(id),
 verified_at TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(account_id,period)
);
ALTER TABLE accounting.bank_statement_lines ADD COLUMN IF NOT EXISTS bank_account_id TEXT
 REFERENCES accounting.bank_accounts(id);
ALTER TABLE accounting.bank_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_statement_coverages ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_statement_coverages FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_accounts_read ON accounting.bank_accounts FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY bank_accounts_write ON accounting.bank_accounts FOR ALL TO accounting_app
 USING(accounting.book_allowed(book_id,true)) WITH CHECK(accounting.book_allowed(book_id,true));
CREATE POLICY bank_coverages_read ON accounting.bank_statement_coverages FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY bank_coverages_write ON accounting.bank_statement_coverages FOR ALL TO accounting_app
 USING(accounting.book_allowed(book_id,true)) WITH CHECK(accounting.book_allowed(book_id,true));
CREATE TRIGGER p0_bank_account_dependency_revision AFTER INSERT OR UPDATE OR DELETE
 ON accounting.bank_accounts FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER p0_bank_coverage_dependency_revision AFTER INSERT OR UPDATE OR DELETE
 ON accounting.bank_statement_coverages FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
