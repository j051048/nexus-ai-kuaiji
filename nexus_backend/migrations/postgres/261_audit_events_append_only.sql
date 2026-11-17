-- OPT03-A: audit events are append-only even for the table owner / migration role.
CREATE OR REPLACE FUNCTION accounting.audit_events_append_only_guard()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'accounting.audit_events is append-only' USING ERRCODE='23514';
END $$;

REVOKE ALL ON FUNCTION accounting.audit_events_append_only_guard() FROM PUBLIC;

CREATE TRIGGER audit_events_append_only_row
BEFORE UPDATE OR DELETE ON accounting.audit_events
FOR EACH ROW EXECUTE FUNCTION accounting.audit_events_append_only_guard();

CREATE TRIGGER audit_events_append_only_truncate
BEFORE TRUNCATE ON accounting.audit_events
FOR EACH STATEMENT EXECUTE FUNCTION accounting.audit_events_append_only_guard();
