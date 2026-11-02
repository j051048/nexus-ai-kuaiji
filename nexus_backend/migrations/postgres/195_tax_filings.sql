-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
-- DCP08-B: filings are recorded by a firm reviewer after the fact; the system
-- never claims to have filed on the client's behalf.
CREATE TABLE IF NOT EXISTS accounting.tax_filings(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 period TEXT NOT NULL CHECK(period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
 kind TEXT NOT NULL CHECK(kind IN('vat','surtax')),
 status TEXT NOT NULL CHECK(status IN('filed','paid')),
 external_ref TEXT NOT NULL CHECK(char_length(external_ref) BETWEEN 3 AND 100),
 occurred_at TEXT NOT NULL CHECK(char_length(occurred_at)=10),
 amount_cents BIGINT NOT NULL DEFAULT 0 CHECK(amount_cents>=0),
 evidence_note TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tax_filings_book ON accounting.tax_filings(book_id,period,occurred_at);
CREATE OR REPLACE FUNCTION accounting.tax_filings_append_only() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'tax_filings is append-only';
END;
$$;
DROP TRIGGER IF EXISTS tax_filings_append_only ON accounting.tax_filings;
CREATE TRIGGER tax_filings_append_only
 BEFORE UPDATE OR DELETE ON accounting.tax_filings
 FOR EACH ROW EXECUTE FUNCTION accounting.tax_filings_append_only();
