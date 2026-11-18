-- PLT02-B: trial/formal report modes and policy approval freeze.
ALTER TABLE accounting.accounting_policies ADD COLUMN approved_by TEXT;
ALTER TABLE accounting.accounting_policies ADD COLUMN approved_at TIMESTAMPTZ;
ALTER TABLE accounting.accounting_policies ADD COLUMN is_synthetic BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE accounting.accounting_policies ADD COLUMN mapping_fingerprint TEXT;

ALTER TABLE accounting.report_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'formal'
    CHECK(mode IN ('trial','formal'));
ALTER TABLE accounting.report_runs ADD COLUMN standard_kind TEXT;
ALTER TABLE accounting.report_runs ADD COLUMN policy_version INTEGER;
ALTER TABLE accounting.report_runs ADD COLUMN policy_approver TEXT;
ALTER TABLE accounting.report_runs ADD COLUMN mapping_fingerprint TEXT;
ALTER TABLE accounting.report_runs ADD COLUMN trial_reasons_json TEXT;

CREATE OR REPLACE FUNCTION accounting.report_formal_publish_guard()
RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'published' AND NEW.mode IS DISTINCT FROM 'formal' THEN
        RAISE EXCEPTION '试算报表不能发布';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS report_runs_formal_publish ON accounting.report_runs;
CREATE TRIGGER report_runs_formal_publish
BEFORE INSERT OR UPDATE OF status ON accounting.report_runs
FOR EACH ROW EXECUTE FUNCTION accounting.report_formal_publish_guard();
