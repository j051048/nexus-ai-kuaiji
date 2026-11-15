-- G1: independent external-ledger evidence, append-only after verification.
CREATE TABLE accounting.monthly_external_evidence(
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 period TEXT NOT NULL,
 report_id TEXT NOT NULL REFERENCES accounting.report_runs(id),
 report_version INTEGER NOT NULL CHECK(report_version>0),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('tplus_report','import_result')),
 source_name TEXT NOT NULL CHECK(length(source_name) BETWEEN 3 AND 200),
 source_version TEXT NOT NULL CHECK(length(source_version) BETWEEN 1 AND 100),
 content_text TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 status TEXT NOT NULL CHECK(status IN ('pending','verified','rejected')),
 note TEXT NOT NULL CHECK(length(note) BETWEEN 3 AND 500),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 verified_by TEXT REFERENCES accounting.users(id),
 verified_at TEXT,
 review_note TEXT,
 fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 UNIQUE(book_id,report_id,report_version,source_kind,source_version,sha256),
 UNIQUE(id,book_id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE INDEX idx_external_evidence_scope ON accounting.monthly_external_evidence(book_id,report_id,report_version,status);

ALTER TABLE accounting.monthly_external_evidence ENABLE ROW LEVEL SECURITY;
CREATE POLICY monthly_external_evidence_company ON accounting.monthly_external_evidence
 USING (accounting.company_visible(company_id))
 WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.monthly_external_evidence TO accounting_app;
GRANT SELECT ON accounting.monthly_external_evidence TO accounting_projection;

CREATE OR REPLACE FUNCTION accounting.guard_monthly_external_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'External evidence is append-only'; END IF;
 IF OLD.id<>NEW.id OR OLD.book_id<>NEW.book_id OR OLD.firm_id<>NEW.firm_id
    OR OLD.company_id<>NEW.company_id OR OLD.report_id<>NEW.report_id
    OR OLD.report_version<>NEW.report_version OR OLD.source_kind<>NEW.source_kind
    OR OLD.source_name<>NEW.source_name OR OLD.source_version<>NEW.source_version
    OR OLD.content_text<>NEW.content_text OR OLD.sha256<>NEW.sha256
    OR OLD.created_by<>NEW.created_by OR OLD.created_at<>NEW.created_at THEN
   RAISE EXCEPTION 'External evidence identity is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER monthly_external_evidence_guard BEFORE UPDATE OR DELETE ON accounting.monthly_external_evidence
FOR EACH ROW EXECUTE FUNCTION accounting.guard_monthly_external_evidence();
