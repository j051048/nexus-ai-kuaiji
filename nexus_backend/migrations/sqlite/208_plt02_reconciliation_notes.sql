CREATE TABLE IF NOT EXISTS report_reconciliation_notes(
 id TEXT PRIMARY KEY,
 report_id TEXT NOT NULL REFERENCES report_runs(id),
 book_id TEXT NOT NULL,
 firm_id TEXT NOT NULL,
 reference_sha256 TEXT NOT NULL,
 line_key TEXT NOT NULL,
 explanation_type TEXT NOT NULL CHECK(explanation_type IN('rounding','reclassification','basis','unknown')),
 note TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_reconciliation_report
 ON report_reconciliation_notes(report_id, reference_sha256, created_at);

CREATE TRIGGER IF NOT EXISTS report_reconciliation_notes_append_only
BEFORE UPDATE ON report_reconciliation_notes
BEGIN
 SELECT RAISE(ABORT, '报表差异解释不可修改');
END;

CREATE TRIGGER IF NOT EXISTS report_reconciliation_notes_no_delete
BEFORE DELETE ON report_reconciliation_notes
BEGIN
 SELECT RAISE(ABORT, '报表差异解释不可删除');
END;
