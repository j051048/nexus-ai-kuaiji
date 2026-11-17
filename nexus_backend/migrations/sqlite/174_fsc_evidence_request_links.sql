-- Link evidence-policy blockers to the ordinary monthly material-request loop.
-- The request owns its status; this table only records why it was opened.
CREATE TABLE fsc_evidence_request_links(
 request_id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL CHECK(policy_version>0),
 action TEXT NOT NULL,
 business_type TEXT NOT NULL,
 evidence_kind TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 FOREIGN KEY(request_id) REFERENCES material_requests(id),
 FOREIGN KEY(policy_id) REFERENCES fsc_evidence_policies(id)
);
CREATE INDEX idx_fsc_evidence_request_policy ON fsc_evidence_request_links(book_id,policy_id,action,business_type,evidence_kind);
