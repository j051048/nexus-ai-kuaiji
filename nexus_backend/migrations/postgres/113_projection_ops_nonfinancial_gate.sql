-- compatibility: incremental
-- ops_company_dirty/refresh 只是投影维护的非财务脏标记，运行账号仅有 SELECT，
-- 写入仅来自受控 SECURITY DEFINER 触发器。嵌套安全定义函数里 set_config 的作用域
-- 不能稳定传给下级写锁，故对这两张非财务表去掉通用企业写门，避免把合法业务写入
-- 误判为 legacy 全库写；财务表、投影内容表和审计表仍保留企业写门。
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.ops_company_dirty;
DROP TRIGGER IF EXISTS batch2_statement_write_gate ON accounting.ops_company_dirty;
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.ops_company_refresh;
DROP TRIGGER IF EXISTS batch2_statement_write_gate ON accounting.ops_company_refresh;

CREATE OR REPLACE FUNCTION accounting.mark_ops_company_dirty(target_firm text, target_company text)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'accounting', 'pg_temp'
AS $function$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company) THEN RETURN; END IF;
 INSERT INTO accounting.ops_company_dirty(firm_id,company_id,dirty_at) VALUES(target_firm,target_company,clock_timestamp()::text)
 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO accounting.ops_company_refresh(firm_id,company_id,next_due_at) VALUES(target_firm,target_company,clock_timestamp()::text) ON CONFLICT DO NOTHING;
 END $function$;
