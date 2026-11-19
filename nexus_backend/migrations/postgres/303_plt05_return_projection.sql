-- compatibility: incremental
-- PLT05-C: 返回差异进入统一待办投影后，返回结果文件与逐笔处置的写入必须把企业
-- 标记为脏。复用固定的 workspace_projection_bump() 会被其来源白名单拒绝，因此
-- 为两张只追加的返回源表建立专用触发器函数：只接受这两个表、只接受 INSERT，
-- 并以 SECURITY DEFINER 只写修订计数（不写任何账务或任务内容）。
CREATE OR REPLACE FUNCTION accounting.bridge_return_projection_bump() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $bump$
DECLARE
 value JSONB;
 target_firm TEXT;
 target_company TEXT;
BEGIN
 IF TG_TABLE_SCHEMA<>'accounting' OR TG_LEVEL<>'ROW' OR TG_WHEN<>'AFTER'
 OR TG_NAME<>ALL(ARRAY['bridge_return_imports_projection_bump','bridge_return_handlers_projection_bump']) OR TG_NARGS<>0
 OR TG_OP<>'INSERT'
 OR TG_TABLE_NAME<>ALL(ARRAY['bridge_return_imports','bridge_return_row_handlers'])
 OR TG_RELID IS DISTINCT FROM to_regclass(format('accounting.%I',TG_TABLE_NAME)) THEN
  RAISE EXCEPTION 'Untrusted bridge return projection trigger source' USING ERRCODE='42501';
 END IF;
 value:=to_jsonb(NEW);
 target_firm:=value->>'firm_id';
 target_company:=value->>'company_id';
 IF target_firm IS NULL OR target_company IS NULL THEN RETURN NEW; END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.firm_clients fc
  WHERE fc.firm_id=target_firm AND fc.company_id=target_company) THEN
  RAISE EXCEPTION 'Bridge return source has no firm/company relationship' USING ERRCODE='42501';
 END IF;
 INSERT INTO accounting.workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at)
  VALUES(target_firm,target_company,1,0,
   to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'))
  ON CONFLICT(firm_id,company_id) DO UPDATE
   SET revision=accounting.workspace_projection_state.revision+1,dirty_at=EXCLUDED.dirty_at;
 RETURN NEW;
END $bump$;

REVOKE ALL ON FUNCTION accounting.bridge_return_projection_bump() FROM PUBLIC,accounting_app,accounting_worker;

CREATE TRIGGER bridge_return_imports_projection_bump
 AFTER INSERT ON accounting.bridge_return_imports
 FOR EACH ROW EXECUTE FUNCTION accounting.bridge_return_projection_bump();
CREATE TRIGGER bridge_return_handlers_projection_bump
 AFTER INSERT ON accounting.bridge_return_row_handlers
 FOR EACH ROW EXECUTE FUNCTION accounting.bridge_return_projection_bump();
