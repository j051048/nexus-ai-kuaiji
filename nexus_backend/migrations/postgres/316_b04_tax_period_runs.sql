-- compatibility: incremental
-- UPG Batch 04: reviewed four-tax period results.
--
-- A tax period result is a *reviewed artifact*, not a scratch calculation: the
-- payload that produced a payable amount must stay reproducible, so version
-- rows are append-only once reviewed and only a reviewer other than the author
-- may move draft -> reviewed.
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.tax_period_runs (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  period TEXT NOT NULL CHECK(period ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
  kind TEXT NOT NULL CHECK(kind IN ('vat','surtax','cit_prepayment','stamp_duty')),
  status TEXT NOT NULL CHECK(status IN ('draft','reviewed')),
  version INTEGER NOT NULL CHECK(version>=1),
  payload_json JSONB NOT NULL,
  total_cents BIGINT NOT NULL,
  source_digest TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL,
  reviewed_by TEXT REFERENCES accounting.users(id),
  reviewed_at TIMESTAMPTZ,
  review_note TEXT,
  UNIQUE(book_id,period,kind,version),
  CHECK(
    (status='draft' AND reviewed_by IS NULL AND reviewed_at IS NULL)
    OR (status='reviewed' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_tax_period_runs_scope ON accounting.tax_period_runs(book_id,period,kind);

ALTER TABLE accounting.tax_period_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_period_runs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tax_period_runs_read ON accounting.tax_period_runs;
CREATE POLICY tax_period_runs_read ON accounting.tax_period_runs
  FOR SELECT TO accounting_app
  USING (
    accounting.book_allowed(tax_period_runs.book_id)
    AND tax_period_runs.firm_id = accounting.actor_firm()
  );

DROP POLICY IF EXISTS tax_period_runs_write ON accounting.tax_period_runs;
CREATE POLICY tax_period_runs_write ON accounting.tax_period_runs
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_allowed(tax_period_runs.book_id, true)
    AND tax_period_runs.firm_id = accounting.actor_firm()
    AND tax_period_runs.created_by = accounting.actor_user()
    AND tax_period_runs.status = 'draft'
  );

DROP POLICY IF EXISTS tax_period_runs_review ON accounting.tax_period_runs;
CREATE POLICY tax_period_runs_review ON accounting.tax_period_runs
  FOR UPDATE TO accounting_app
  USING (
    accounting.book_review_allowed(tax_period_runs.book_id)
    AND tax_period_runs.firm_id = accounting.actor_firm()
    AND tax_period_runs.status = 'draft'
    AND tax_period_runs.created_by <> accounting.actor_user()
  )
  WITH CHECK (
    accounting.book_review_allowed(tax_period_runs.book_id)
    AND tax_period_runs.status = 'reviewed'
    AND tax_period_runs.reviewed_by = accounting.actor_user()
  );

REVOKE ALL ON accounting.tax_period_runs FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT, UPDATE ON accounting.tax_period_runs TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.tax_period_runs_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'tax period runs are append-only';
  END IF;
  IF OLD.status = 'reviewed' THEN
    RAISE EXCEPTION 'reviewed tax period runs are immutable';
  END IF;
  IF NEW.book_id <> OLD.book_id OR NEW.period <> OLD.period OR NEW.kind <> OLD.kind
     OR NEW.version <> OLD.version OR NEW.payload_json <> OLD.payload_json
     OR NEW.total_cents <> OLD.total_cents OR NEW.source_digest <> OLD.source_digest THEN
    RAISE EXCEPTION 'tax period run payload is immutable';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS tax_period_runs_guard ON accounting.tax_period_runs;
CREATE TRIGGER tax_period_runs_guard
 BEFORE UPDATE OR DELETE ON accounting.tax_period_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.tax_period_runs_immutable();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_period_runs;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_period_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
