-- compatibility: incremental
-- Operational, administrator-only record. Runtime identities cannot release a
-- restored copy or rewrite the review evidence.
CREATE TABLE accounting.recovery_release_audit(
 restore_id TEXT PRIMARY KEY, operator TEXT NOT NULL CHECK(length(btrim(operator))>0),
 reason TEXT NOT NULL CHECK(length(btrim(reason))>0), proof_json TEXT NOT NULL,
 inventory_sha256 TEXT NOT NULL CHECK(length(inventory_sha256)=64), created_at TEXT NOT NULL
);
REVOKE ALL ON accounting.recovery_release_audit FROM PUBLIC,accounting_app,accounting_system,accounting_worker,accounting_projection;
CREATE FUNCTION accounting.recovery_audit_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN RAISE EXCEPTION 'recovery release audit is append only' USING ERRCODE='42501'; END $$;
CREATE TRIGGER recovery_audit_immutable BEFORE UPDATE OR DELETE ON accounting.recovery_release_audit
FOR EACH ROW EXECUTE FUNCTION accounting.recovery_audit_append_only();
REVOKE ALL ON FUNCTION accounting.recovery_audit_append_only() FROM PUBLIC;
