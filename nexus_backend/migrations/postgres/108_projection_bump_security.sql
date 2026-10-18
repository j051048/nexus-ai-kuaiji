-- compatibility: incremental
-- 090 的 projection bump 以调用者身份写入投影状态表，但运行账号只有 SELECT，
-- 导致企业创建等合法写入被 RLS 触发器拒绝。触发器只维护 projection_state，
-- 不接受动态 SQL；改为固定 search_path 的 SECURITY DEFINER，并继续撤销直接执行。
CREATE OR REPLACE FUNCTION accounting.workspace_projection_bump() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE value JSONB;target_firm TEXT;target_company TEXT;
BEGIN
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
 RETURN coalesce(NEW,OLD);
END $$;
REVOKE ALL ON FUNCTION accounting.workspace_projection_bump() FROM PUBLIC,accounting_app,accounting_worker;
