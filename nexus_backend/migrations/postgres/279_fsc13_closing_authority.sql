-- FSC13: external closing evidence is correspondence, not a second posting.
CREATE TABLE accounting.closing_external_evidence(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 period TEXT NOT NULL,
 authority_mode TEXT NOT NULL CHECK(authority_mode IN('internal','tplus_voucher','hybrid')),
 authority_profile_id TEXT NOT NULL,
 source_system TEXT NOT NULL CHECK(length(source_system) BETWEEN 1 AND 80),
 source_object TEXT NOT NULL CHECK(length(source_object) BETWEEN 1 AND 120),
 source_id TEXT NOT NULL CHECK(length(source_id) BETWEEN 1 AND 160),
 source_version TEXT NOT NULL CHECK(length(source_version) BETWEEN 1 AND 80),
 content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),
 amount_cents BIGINT NOT NULL CHECK(amount_cents>=0),
 status TEXT NOT NULL CHECK(status IN('matched','conflict','evidence_only','needs_review')),
 details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 UNIQUE(book_id,source_system,source_object,source_id,source_version)
);
CREATE INDEX idx_closing_external_book ON accounting.closing_external_evidence(book_id,period,created_at);
ALTER TABLE accounting.closing_external_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.closing_external_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY closing_external_company ON accounting.closing_external_evidence
 FOR ALL USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.closing_external_evidence TO accounting_app;
