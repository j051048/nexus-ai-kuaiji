-- compatibility: breaking
CREATE TABLE accounting.oa_approval_snapshots(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 oa_event_id TEXT NOT NULL,oa_version INTEGER NOT NULL CHECK(oa_version>0),period TEXT NOT NULL,
 title TEXT NOT NULL,approved_by TEXT NOT NULL,approved_at TEXT NOT NULL,
 lines_json JSONB NOT NULL,source_sha256 TEXT NOT NULL,fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL,
 UNIQUE(firm_id,company_id,book_id,oa_event_id,oa_version),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
ALTER TABLE accounting.oa_approval_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY oa_snapshot_scope ON accounting.oa_approval_snapshots USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT ON accounting.oa_approval_snapshots TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.oa_snapshot_immutable_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN RAISE EXCEPTION 'OA approval snapshot is immutable' USING ERRCODE='23514'; END $$;
DROP TRIGGER IF EXISTS oa_snapshot_identity ON accounting.oa_approval_snapshots;
CREATE TRIGGER oa_snapshot_identity BEFORE UPDATE ON accounting.oa_approval_snapshots FOR EACH ROW EXECUTE FUNCTION accounting.oa_snapshot_immutable_guard();
DROP TRIGGER IF EXISTS oa_snapshot_delete ON accounting.oa_approval_snapshots;
CREATE TRIGGER oa_snapshot_delete BEFORE DELETE ON accounting.oa_approval_snapshots FOR EACH ROW EXECUTE FUNCTION accounting.oa_snapshot_immutable_guard();
