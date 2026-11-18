CREATE TABLE IF NOT EXISTS accounting.report_reconciliation_notes(
 id TEXT PRIMARY KEY,
 report_id TEXT NOT NULL REFERENCES accounting.report_runs(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 firm_id TEXT NOT NULL,
 reference_sha256 TEXT NOT NULL,
 line_key TEXT NOT NULL,
 explanation_type TEXT NOT NULL CHECK(explanation_type IN('rounding','reclassification','basis','unknown')),
 note TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_reconciliation_report
 ON accounting.report_reconciliation_notes(report_id, reference_sha256, created_at);

ALTER TABLE accounting.report_reconciliation_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.report_reconciliation_notes FORCE ROW LEVEL SECURITY;
CREATE POLICY report_reconciliation_read ON accounting.report_reconciliation_notes
 FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY report_reconciliation_insert ON accounting.report_reconciliation_notes
 FOR INSERT TO accounting_app
 WITH CHECK(accounting.book_allowed(book_id,TRUE));
GRANT SELECT,INSERT ON accounting.report_reconciliation_notes TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.plt02_reconciliation_guard()
RETURNS TRIGGER LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP IN ('UPDATE','DELETE') THEN
  RAISE EXCEPTION '报表差异解释不可修改或删除' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS report_reconciliation_append_only ON accounting.report_reconciliation_notes;
CREATE TRIGGER report_reconciliation_append_only
BEFORE UPDATE OR DELETE ON accounting.report_reconciliation_notes
FOR EACH ROW EXECUTE FUNCTION accounting.plt02_reconciliation_guard();
