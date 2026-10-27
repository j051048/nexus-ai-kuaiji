-- compatibility: incremental
-- Mirrors postgres/169_tax_workpapers.sql for the local demo engine.
-- SQLite has no RLS; the service still enforces book access, immutability and
-- the independent review gate so both engines answer the same commands alike.
CREATE TABLE IF NOT EXISTS tax_profiles(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 effective_from TEXT NOT NULL CHECK(
  length(effective_from)=7 AND effective_from GLOB '20[0-9][0-9]-[01][0-9]'
  AND substr(effective_from,6,2) BETWEEN '01' AND '12'),
 taxpayer_type TEXT NOT NULL CHECK(taxpayer_type IN('small_scale','general')),
 vat_rate_bp INTEGER NOT NULL CHECK(vat_rate_bp BETWEEN 0 AND 10000),
 surtax_rate_bp INTEGER NOT NULL DEFAULT 0 CHECK(surtax_rate_bp BETWEEN 0 AND 10000),
 filing_cycle TEXT NOT NULL DEFAULT 'monthly' CHECK(filing_cycle IN('monthly','quarterly')),
 reason TEXT NOT NULL CHECK(length(reason) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 UNIQUE(company_id,effective_from)
);
CREATE INDEX IF NOT EXISTS idx_tax_profiles_company
 ON tax_profiles(firm_id,company_id,effective_from DESC);

CREATE TABLE IF NOT EXISTS tax_workpapers(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL CHECK(
  length(period)=7 AND period GLOB '20[0-9][0-9]-[01][0-9]'
  AND substr(period,6,2) BETWEEN '01' AND '12'),
 version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL CHECK(status IN('draft','reviewed')),
 profile_id TEXT NOT NULL REFERENCES tax_profiles(id),
 output_net_cents INTEGER NOT NULL CHECK(output_net_cents>=0),
 output_tax_cents INTEGER NOT NULL CHECK(output_tax_cents>=0),
 input_net_cents INTEGER NOT NULL CHECK(input_net_cents>=0),
 input_tax_cents INTEGER NOT NULL CHECK(input_tax_cents>=0),
 tax_cents INTEGER NOT NULL CHECK(tax_cents>=0),
 surtax_cents INTEGER NOT NULL CHECK(surtax_cents>=0),
 credit_carry_cents INTEGER NOT NULL DEFAULT 0 CHECK(credit_carry_cents>=0),
 ledger_cents INTEGER NOT NULL,
 variance_cents INTEGER NOT NULL,
 variance_reason TEXT NOT NULL DEFAULT '',
 lines_json TEXT NOT NULL,
 source_digest TEXT NOT NULL CHECK(length(source_digest)=64),
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 reviewed_by TEXT REFERENCES users(id),
 reviewed_at TEXT,
 review_reason TEXT NOT NULL DEFAULT '',
 UNIQUE(book_id,period,version)
);
CREATE INDEX IF NOT EXISTS idx_tax_workpapers_book
 ON tax_workpapers(firm_id,book_id,period,version DESC);

CREATE TRIGGER IF NOT EXISTS tax_workpaper_numbers_immutable
 BEFORE UPDATE ON tax_workpapers
 WHEN OLD.status='reviewed'
 BEGIN
  SELECT RAISE(ABORT,'Reviewed workpaper numbers are immutable');
 END;
CREATE TRIGGER IF NOT EXISTS tax_workpaper_no_delete
 BEFORE DELETE ON tax_workpapers
 BEGIN
  SELECT RAISE(ABORT,'Tax workpaper cannot be deleted');
 END;
