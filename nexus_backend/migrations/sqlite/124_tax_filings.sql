-- compatibility: incremental
-- DCP08-B：外部办理结果只能人工登记，系统不代客户申报，也不改写正式账。
CREATE TABLE IF NOT EXISTS tax_filings(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL CHECK(period GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
 kind TEXT NOT NULL CHECK(kind IN('vat','surtax')),
 status TEXT NOT NULL CHECK(status IN('filed','paid')),
 external_ref TEXT NOT NULL CHECK(length(external_ref) BETWEEN 3 AND 100),
 occurred_at TEXT NOT NULL CHECK(length(occurred_at)=10),
 amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(amount_cents>=0),
 evidence_note TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tax_filings_book ON tax_filings(book_id,period,occurred_at);
CREATE TRIGGER IF NOT EXISTS tax_filings_append_only
 BEFORE UPDATE ON tax_filings
 BEGIN SELECT RAISE(ABORT,'tax_filings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS tax_filings_no_delete
 BEFORE DELETE ON tax_filings
 BEGIN SELECT RAISE(ABORT,'tax_filings is append-only'); END;
