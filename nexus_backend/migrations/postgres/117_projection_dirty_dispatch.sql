-- compatibility: incremental
-- Projection revision counters are derived metadata, not ledger/task content.
-- A dispatcher source update already holds its source-row lock. Taking a new
-- company lock from its AFTER trigger would invert the company->source order.
-- Keep the dispatcher allowlist and all financial/task-content gates intact;
-- instead guard this metadata-only write through the real, closed source list.

CREATE OR REPLACE FUNCTION accounting.workspace_projection_bump() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $bump$
DECLARE
 value JSONB;
 target_firm TEXT;
 target_company TEXT;
 scope_firm TEXT:=coalesce(current_setting('app.write_scope_firm',true),'');
 scope_company TEXT:=coalesce(current_setting('app.write_scope_company',true),'');
BEGIN
 IF TG_TABLE_SCHEMA<>'accounting' OR TG_LEVEL<>'ROW' OR TG_WHEN<>'AFTER'
 OR TG_NAME<>'workspace_projection_bump' OR TG_NARGS<>0
 OR TG_OP NOT IN('INSERT','UPDATE','DELETE')
 OR TG_TABLE_NAME<>ALL(ARRAY[
  'intake_drafts','intake_runs','business_events','business_mappings',
  'case_tasks','case_responses','ai_bank_links','proposal_jobs','ocr_jobs',
  'delivery_receipts','ops_alerts','material_requests','material_request_actions',
  'companies','firm_clients','drafts','sources','periods','exports',
  'bank_source_dispositions'
 ])
 OR TG_RELID IS DISTINCT FROM to_regclass(format('accounting.%I',TG_TABLE_NAME)) THEN
  RAISE EXCEPTION 'Untrusted workspace projection trigger source' USING ERRCODE='42501';
 END IF;
 value:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 IF TG_TABLE_NAME IN('drafts','sources','periods','exports','bank_source_dispositions') THEN
  SELECT b.firm_id,b.company_id INTO target_firm,target_company
   FROM accounting.books b WHERE b.id=value->>'book_id';
 ELSIF TG_TABLE_NAME='companies' THEN
  target_company:=value->>'id';
  IF scope_company<>'' AND target_company IS DISTINCT FROM scope_company THEN
   RAISE EXCEPTION 'Projection source differs from declared company scope' USING ERRCODE='42501';
  END IF;
  -- One physical company record can be linked to multiple firms. Its actual
  -- mutation dirties only those database-backed links, never caller targets.
  INSERT INTO accounting.workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at)
   SELECT fc.firm_id,target_company,1,0,
    to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
   FROM accounting.firm_clients fc WHERE fc.company_id=target_company
   ON CONFLICT(firm_id,company_id) DO UPDATE
    SET revision=accounting.workspace_projection_state.revision+1,dirty_at=EXCLUDED.dirty_at;
  RETURN coalesce(NEW,OLD);
 ELSE
  target_firm:=value->>'firm_id';
  target_company:=value->>'company_id';
 END IF;
 -- A book may already have been removed by a legitimate DELETE. There is no
 -- current projection scope to refresh in that case, matching the old behavior.
 IF target_firm IS NULL OR target_company IS NULL THEN RETURN coalesce(NEW,OLD);END IF;
 IF scope_company<>'' AND (target_firm IS DISTINCT FROM scope_firm
 OR target_company IS DISTINCT FROM scope_company) THEN
  RAISE EXCEPTION 'Projection source differs from declared company scope' USING ERRCODE='42501';
 END IF;
 IF NOT (TG_TABLE_NAME='firm_clients' AND TG_OP='DELETE')
 AND NOT EXISTS(SELECT 1 FROM accounting.firm_clients fc
  WHERE fc.firm_id=target_firm AND fc.company_id=target_company) THEN
  RAISE EXCEPTION 'Projection source has no firm/company relationship' USING ERRCODE='42501';
 END IF;
 INSERT INTO accounting.workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at)
  VALUES(target_firm,target_company,1,0,
   to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'))
  ON CONFLICT(firm_id,company_id) DO UPDATE
   SET revision=accounting.workspace_projection_state.revision+1,dirty_at=EXCLUDED.dirty_at;
 RETURN coalesce(NEW,OLD);
END $bump$;

REVOKE ALL ON FUNCTION accounting.workspace_projection_bump() FROM PUBLIC,accounting_app,accounting_worker;
REVOKE ALL ON accounting.workspace_projection_state FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT ON accounting.workspace_projection_state TO accounting_app;

-- Only the revision/dirty-state table is detached. RLS, system maintenance
-- permissions, source-table guards and workspace_task_projections stay intact.
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.workspace_projection_state;
DROP TRIGGER IF EXISTS batch2_statement_write_gate ON accounting.workspace_projection_state;
