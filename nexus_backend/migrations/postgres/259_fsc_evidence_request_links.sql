CREATE TABLE accounting.fsc_evidence_request_links(
 request_id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL CHECK(policy_version>0),
 action TEXT NOT NULL,
 business_type TEXT NOT NULL,
 evidence_kind TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 FOREIGN KEY(request_id) REFERENCES accounting.material_requests(id),
 FOREIGN KEY(policy_id) REFERENCES accounting.fsc_evidence_policies(id)
);
CREATE INDEX idx_fsc_evidence_request_policy
 ON accounting.fsc_evidence_request_links(book_id,policy_id,action,business_type,evidence_kind);
ALTER TABLE accounting.fsc_evidence_request_links ENABLE ROW LEVEL SECURITY;
CREATE POLICY fsc_evidence_request_links_company ON accounting.fsc_evidence_request_links
 USING (accounting.company_visible(company_id))
 WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT ON accounting.fsc_evidence_request_links TO accounting_app;
