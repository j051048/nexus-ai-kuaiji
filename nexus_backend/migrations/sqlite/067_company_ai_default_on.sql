-- Temporary migration inventory provides the same attributable defaults as PG.
CREATE TEMP TABLE company_ai_default_candidates AS
 SELECT fc.firm_id,fc.company_id,COALESCE(
   (SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id AND u.active=1
    WHERE m.firm_id=fc.firm_id AND m.role IN('owner','admin')
    ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END,m.user_id LIMIT 1),
   (SELECT cm.user_id FROM company_memberships cm JOIN users u ON u.id=cm.user_id AND u.active=1
    WHERE cm.company_id=fc.company_id AND cm.role IN('owner','admin')
    ORDER BY CASE cm.role WHEN 'owner' THEN 0 ELSE 1 END,cm.user_id LIMIT 1)
 ) AS updated_by,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') AS updated_at
 FROM firm_clients fc JOIN companies c ON c.id=fc.company_id AND c.status='active'
 WHERE fc.status='active' AND NOT EXISTS(SELECT 1 FROM ai_company_settings s WHERE s.firm_id=fc.firm_id AND s.company_id=fc.company_id);
INSERT INTO ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at)
 SELECT firm_id,company_id,1,updated_by,updated_at FROM company_ai_default_candidates WHERE updated_by IS NOT NULL;
INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at,company_id)
 SELECT firm_id,updated_by,'admin.ai.default_enabled',company_id,
 json_object('company_id',company_id,'enabled',json('true'),'source','migration_default_on',
  'reason','未配置企业按产品默认启用AI处理；仍受供应商、配额及人工复核约束'),updated_at,company_id
 FROM company_ai_default_candidates WHERE updated_by IS NOT NULL;
DROP TABLE company_ai_default_candidates;
