CREATE TABLE accounting.oa_material_requests(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL REFERENCES accounting.oa_approval_snapshots(id),title TEXT NOT NULL,reason TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('open','closed')),version INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL,UNIQUE(book_id,snapshot_id)
);
ALTER TABLE accounting.oa_material_requests ENABLE ROW LEVEL SECURITY;
CREATE POLICY oa_material_scope ON accounting.oa_material_requests USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.oa_material_requests TO accounting_app;
