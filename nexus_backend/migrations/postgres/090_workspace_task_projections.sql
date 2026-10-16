-- compatibility: incremental
-- 统一待办投影（PostgreSQL）：源表写入通过触发器把企业标记为脏；读取路径在干净时
-- 使用投影、脏时回退实时查询。投影行的读取仍受逐行 RLS 保护——策略逐行调用与源表
-- 相同的可见性函数（business_event_visible / intake_allowed / case_manage /
-- book_allowed / proposal_scope / ops_company_allowed / service_material_visible /
-- migration_allowed），因此不会放宽任何一行的暴露面。刷新由 API 进程的系统身份
-- 在全局写门内执行（写入方为 accounting_system，读取源表也已逐一授权）。
CREATE TABLE accounting.workspace_projection_state(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1,built_revision INTEGER NOT NULL DEFAULT 0,
 dirty_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id)
);
CREATE TABLE accounting.workspace_task_projections(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 kind TEXT NOT NULL,ref TEXT NOT NULL,
 book_id TEXT,event_id TEXT,draft_id TEXT,batch_id TEXT,
 applicant_id TEXT,department_id TEXT,assignee_id TEXT,recipient_id TEXT,created_by TEXT,
 submission_mode TEXT NOT NULL DEFAULT '',ref_kind TEXT NOT NULL DEFAULT '',
 ref_detail TEXT NOT NULL DEFAULT '',
 source_open INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL,title TEXT NOT NULL,due_on TEXT,updated_at TEXT NOT NULL,
 amount_cents BIGINT,
 PRIMARY KEY(firm_id,company_id,kind,ref)
);
CREATE INDEX idx_workspace_projection_kind ON accounting.workspace_task_projections(firm_id,kind,company_id);
CREATE INDEX idx_workspace_projection_book ON accounting.workspace_task_projections(firm_id,book_id);

CREATE FUNCTION accounting.workspace_projection_bump() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE value JSONB;target_firm TEXT;target_company TEXT;BEGIN
 value:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 IF TG_TABLE_NAME IN('drafts','sources','periods','exports','bank_source_dispositions') THEN
  SELECT b.firm_id,b.company_id INTO target_firm,target_company FROM accounting.books b WHERE b.id=value->>'book_id';
 ELSIF TG_TABLE_NAME='companies' THEN
  INSERT INTO accounting.workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at)
   SELECT fc.firm_id,NEW.id,1,0,to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') FROM accounting.firm_clients fc WHERE fc.company_id=NEW.id
   ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=accounting.workspace_projection_state.revision+1,dirty_at=EXCLUDED.dirty_at;
  RETURN NEW;
 ELSE
  target_firm:=value->>'firm_id';target_company:=value->>'company_id';
 END IF;
 IF target_firm IS NULL OR target_company IS NULL THEN RETURN coalesce(NEW,OLD);END IF;
 INSERT INTO accounting.workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at)
  VALUES(target_firm,target_company,1,0,to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'))
  ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=accounting.workspace_projection_state.revision+1,dirty_at=EXCLUDED.dirty_at;
 RETURN coalesce(NEW,OLD);END $$;
DO $$ DECLARE item TEXT;BEGIN
 FOREACH item IN ARRAY ARRAY['intake_drafts','intake_runs','business_events','business_mappings','case_tasks','case_responses','ai_bank_links','proposal_jobs','ocr_jobs','delivery_receipts','ops_alerts','material_requests','material_request_actions','companies','firm_clients','drafts','sources','periods','exports','bank_source_dispositions'] LOOP
  EXECUTE format('CREATE TRIGGER workspace_projection_bump AFTER INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.workspace_projection_bump()',item);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION accounting.workspace_projection_bump() FROM PUBLIC,accounting_app,accounting_worker;

ALTER TABLE accounting.workspace_projection_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.workspace_projection_state FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.workspace_task_projections ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.workspace_task_projections FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_state_read ON accounting.workspace_projection_state FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm());
CREATE POLICY workspace_state_system ON accounting.workspace_projection_state TO accounting_system
 USING(true) WITH CHECK(true);
CREATE POLICY workspace_projection_read ON accounting.workspace_task_projections FOR SELECT TO accounting_app USING(
 firm_id=accounting.actor_firm() AND CASE kind
  WHEN 'business' THEN accounting.business_event_visible(event_id,firm_id,company_id)
  WHEN 'approval' THEN accounting.business_event_visible(event_id,firm_id,company_id)
  WHEN 'handoff' THEN accounting.business_event_visible(event_id,firm_id,company_id)
  WHEN 'finance' THEN accounting.business_event_visible(event_id,firm_id,company_id)
  WHEN 'ocr' THEN accounting.business_event_visible(event_id,firm_id,company_id)
  WHEN 'intake' THEN accounting.intake_allowed(ref)
  WHEN 'case' THEN accounting.case_manage(ref) OR accounting.case_recipient(ref)
  WHEN 'review' THEN accounting.book_allowed(book_id)
  WHEN 'export' THEN accounting.book_allowed(book_id)
  WHEN 'delivery' THEN accounting.book_allowed(book_id)
  WHEN 'proposal' THEN accounting.proposal_scope(ref)
  WHEN 'alert' THEN accounting.ops_company_allowed(firm_id,company_id)
  WHEN 'material' THEN accounting.service_material_visible(ref)
  WHEN 'migration' THEN accounting.migration_allowed(ref,'viewer')
  ELSE false END);
CREATE POLICY workspace_projection_system ON accounting.workspace_task_projections TO accounting_system
 USING(true) WITH CHECK(true);

REVOKE ALL ON accounting.workspace_projection_state,accounting.workspace_task_projections FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT ON accounting.workspace_projection_state,accounting.workspace_task_projections TO accounting_app;
GRANT SELECT,INSERT,DELETE ON accounting.workspace_task_projections TO accounting_system;
GRANT SELECT,INSERT,UPDATE ON accounting.workspace_projection_state TO accounting_system;

-- 系统身份刷新投影时需要读取的源表：只授 SELECT，且策略为只读全量；写权限完全不放宽。
DO $$ DECLARE item TEXT;BEGIN
 FOREACH item IN ARRAY ARRAY['intake_drafts','business_events','business_mappings','case_tasks','drafts','sources','periods','exports','delivery_receipts','delivery_receipt_items','delivery_manifests','ops_alerts','ocr_jobs','proposal_jobs','material_requests','material_request_actions','ai_bank_links','books','bank_source_dispositions','firm_clients'] LOOP
  EXECUTE format('GRANT SELECT ON accounting.%I TO accounting_system',item);
  EXECUTE format('DROP POLICY IF EXISTS workspace_refresh_read ON accounting.%I',item);
  EXECUTE format('CREATE POLICY workspace_refresh_read ON accounting.%I FOR SELECT TO accounting_system USING(true)',item);
 END LOOP;
END $$;

CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.workspace_projection_state FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.workspace_projection_state FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.workspace_task_projections FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.workspace_task_projections FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
