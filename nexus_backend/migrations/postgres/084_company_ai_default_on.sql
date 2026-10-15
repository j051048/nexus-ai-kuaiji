-- Materialize only unconfigured active companies. Explicit pauses and archived
-- companies are preserved; existing enabled-row SQL gates remain fail closed.
WITH candidates AS (
 SELECT fc.firm_id,fc.company_id,COALESCE(
   (SELECT m.user_id FROM accounting.memberships m JOIN accounting.users u ON u.id=m.user_id AND u.active=1
    WHERE m.firm_id=fc.firm_id AND m.role IN('owner','admin')
    ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END,m.user_id LIMIT 1),
   (SELECT cm.user_id FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id AND u.active=1
    WHERE cm.company_id=fc.company_id AND cm.role IN('owner','admin')
    ORDER BY CASE cm.role WHEN 'owner' THEN 0 ELSE 1 END,cm.user_id LIMIT 1)
 ) AS updated_by
 FROM accounting.firm_clients fc JOIN accounting.companies c ON c.id=fc.company_id AND c.status='active'
 WHERE fc.status='active' AND NOT EXISTS(SELECT 1 FROM accounting.ai_company_settings s WHERE s.firm_id=fc.firm_id AND s.company_id=fc.company_id)
), inserted AS (
 INSERT INTO accounting.ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at)
 SELECT firm_id,company_id,1,updated_by,to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
 FROM candidates WHERE updated_by IS NOT NULL ON CONFLICT(firm_id,company_id) DO NOTHING
 RETURNING firm_id,company_id,updated_by,updated_at
)
INSERT INTO accounting.audit_events(firm_id,user_id,action,object_id,details,created_at,company_id)
 SELECT firm_id,updated_by,'admin.ai.default_enabled',company_id,
 jsonb_build_object('company_id',company_id,'enabled',true,'source','migration_default_on',
  'reason','未配置企业按产品默认启用AI处理；仍受供应商、配额及人工复核约束')::text,updated_at,company_id
 FROM inserted;
