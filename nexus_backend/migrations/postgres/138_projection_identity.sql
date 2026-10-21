-- Dedicated projection identity. API role is never a member.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='accounting_projection') THEN
  CREATE ROLE accounting_projection NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;

REVOKE accounting_projection FROM accounting_app, accounting_system;
GRANT USAGE ON SCHEMA accounting TO accounting_projection;

DO $$ DECLARE item TEXT;BEGIN
 FOREACH item IN ARRAY ARRAY['intake_drafts','business_events','business_mappings','case_tasks','drafts','sources','periods','exports','delivery_receipts','delivery_receipt_items','delivery_manifests','ops_alerts','ocr_jobs','proposal_jobs','material_requests','material_request_actions','ai_bank_links','books','bank_source_dispositions','firm_clients'] LOOP
  EXECUTE format('GRANT SELECT ON accounting.%I TO accounting_projection',item);
  EXECUTE format('DROP POLICY IF EXISTS workspace_projection_source_read ON accounting.%I',item);
  EXECUTE format('CREATE POLICY workspace_projection_source_read ON accounting.%I FOR SELECT TO accounting_projection USING(true)',item);
  EXECUTE format('REVOKE SELECT ON accounting.%I FROM accounting_system',item);
  EXECUTE format('DROP POLICY IF EXISTS workspace_refresh_read ON accounting.%I',item);
 END LOOP;
END $$;

GRANT SELECT,INSERT,DELETE ON accounting.workspace_task_projections TO accounting_projection;
GRANT SELECT,INSERT,UPDATE ON accounting.workspace_projection_state TO accounting_projection;
DROP POLICY IF EXISTS workspace_projection_refresh ON accounting.workspace_task_projections;
CREATE POLICY workspace_projection_refresh ON accounting.workspace_task_projections
 TO accounting_projection USING(true) WITH CHECK(true);
DROP POLICY IF EXISTS workspace_state_projection ON accounting.workspace_projection_state;
CREATE POLICY workspace_state_projection ON accounting.workspace_projection_state
 TO accounting_projection USING(true) WITH CHECK(true);

GRANT EXECUTE ON FUNCTION accounting.begin_company_write(TEXT,TEXT),
 accounting.lock_company_write(TEXT,TEXT),
 accounting.company_lock_key(TEXT,TEXT),
 accounting.lock_legacy_write() TO accounting_projection;
