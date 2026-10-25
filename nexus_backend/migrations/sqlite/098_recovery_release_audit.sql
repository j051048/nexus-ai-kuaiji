-- compatibility: incremental
CREATE TABLE recovery_release_audit(
 restore_id TEXT PRIMARY KEY, operator TEXT NOT NULL CHECK(length(trim(operator))>0),
 reason TEXT NOT NULL CHECK(length(trim(reason))>0), proof_json TEXT NOT NULL,
 inventory_sha256 TEXT NOT NULL CHECK(length(inventory_sha256)=64), created_at TEXT NOT NULL
);
CREATE TRIGGER recovery_audit_no_update BEFORE UPDATE ON recovery_release_audit
BEGIN SELECT RAISE(ABORT,'recovery release audit is append only'); END;
CREATE TRIGGER recovery_audit_no_delete BEFORE DELETE ON recovery_release_audit
BEGIN SELECT RAISE(ABORT,'recovery release audit is append only'); END;
