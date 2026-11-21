-- compatibility: incremental
-- UPG Batch 04 (SQLite mirror of postgres/316): reviewed four-tax period results.
CREATE TABLE tax_period_runs (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  period TEXT NOT NULL CHECK(period GLOB '20[0-9][0-9]-[01][0-9]'),
  kind TEXT NOT NULL CHECK(kind IN ('vat','surtax','cit_prepayment','stamp_duty')),
  status TEXT NOT NULL CHECK(status IN ('draft','reviewed')),
  version INTEGER NOT NULL CHECK(version>=1),
  payload_json TEXT NOT NULL,
  total_cents INTEGER NOT NULL,
  source_digest TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  reviewed_by TEXT REFERENCES users(id),
  reviewed_at TEXT,
  review_note TEXT,
  UNIQUE(book_id,period,kind,version),
  CHECK(
    (status='draft' AND reviewed_by IS NULL AND reviewed_at IS NULL)
    OR (status='reviewed' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
  )
);
CREATE INDEX idx_tax_period_runs_scope ON tax_period_runs(book_id,period,kind);

CREATE TRIGGER tax_period_runs_no_delete
 BEFORE DELETE ON tax_period_runs
 FOR EACH ROW BEGIN
  SELECT RAISE(ABORT,'tax period runs are append-only');
 END;

CREATE TRIGGER tax_period_runs_immutable
 BEFORE UPDATE ON tax_period_runs
 FOR EACH ROW WHEN OLD.status='reviewed'
  OR NEW.book_id<>OLD.book_id OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind
  OR NEW.version<>OLD.version OR NEW.payload_json<>OLD.payload_json
  OR NEW.total_cents<>OLD.total_cents OR NEW.source_digest<>OLD.source_digest
 BEGIN
  SELECT RAISE(ABORT,'reviewed tax period runs are immutable');
 END;
