-- PLT02-B: trial/formal report modes and policy approval freeze.
ALTER TABLE accounting_policies ADD COLUMN approved_by TEXT;
ALTER TABLE accounting_policies ADD COLUMN approved_at TEXT;
ALTER TABLE accounting_policies ADD COLUMN is_synthetic INTEGER NOT NULL DEFAULT 0;
ALTER TABLE accounting_policies ADD COLUMN mapping_fingerprint TEXT;

ALTER TABLE report_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'formal'
    CHECK(mode IN ('trial','formal'));
ALTER TABLE report_runs ADD COLUMN standard_kind TEXT;
ALTER TABLE report_runs ADD COLUMN policy_version INTEGER;
ALTER TABLE report_runs ADD COLUMN policy_approver TEXT;
ALTER TABLE report_runs ADD COLUMN mapping_fingerprint TEXT;
ALTER TABLE report_runs ADD COLUMN trial_reasons_json TEXT;

CREATE TRIGGER IF NOT EXISTS report_runs_formal_publish_insert
BEFORE INSERT ON report_runs
WHEN NEW.status='published' AND NEW.mode<>'formal'
BEGIN
    SELECT RAISE(ABORT, '试算报表不能发布');
END;

CREATE TRIGGER IF NOT EXISTS report_runs_formal_publish_update
BEFORE UPDATE OF status ON report_runs
WHEN NEW.status='published' AND NEW.mode<>'formal'
BEGIN
    SELECT RAISE(ABORT, '试算报表不能发布');
END;
