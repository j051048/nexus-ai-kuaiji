-- compatibility: incremental
-- In-app notices share the source transaction; restore hold never schedules them.
CREATE TRIGGER IF NOT EXISTS client_notice_return AFTER INSERT ON business_actions
WHEN NEW.decision='return' AND NOT EXISTS(SELECT 1 FROM restore_state WHERE hold<>0 OR hold IS NULL)
BEGIN
 INSERT OR IGNORE INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,body,related_kind,related_ref,created_by,created_at)
 SELECT 'task:business_actions:'||NEW.id,NEW.firm_id,NEW.company_id,applicant_id,'return','事项已退回，请补充后重新提交',
 '打开原事项查看当前进度与处理要求；已读不代表审批或签收。','business',NEW.event_id,NEW.created_by,NEW.created_at
 FROM business_events WHERE id=NEW.event_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id;
END;
CREATE TRIGGER IF NOT EXISTS client_notice_material AFTER INSERT ON material_requests
WHEN NEW.assignee_user_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM restore_state WHERE hold<>0 OR hold IS NULL)
BEGIN
 INSERT OR IGNORE INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,body,related_kind,related_ref,created_by,created_at)
 VALUES('task:material_requests:'||NEW.id,NEW.firm_id,NEW.company_id,NEW.assignee_user_id,'reminder','有一项资料需要你提供',
 '打开原事项查看当前进度与处理要求；已读不代表审批或签收。','material',NEW.id,NEW.created_by,NEW.created_at);
END;
CREATE TRIGGER IF NOT EXISTS client_notice_material_return AFTER INSERT ON material_request_actions
WHEN NEW.action='return' AND NOT EXISTS(SELECT 1 FROM restore_state WHERE hold<>0 OR hold IS NULL)
BEGIN
 INSERT OR IGNORE INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,body,related_kind,related_ref,created_by,created_at)
 SELECT 'task:material_request_actions:'||NEW.id,NEW.firm_id,NEW.company_id,assignee_user_id,'return','资料需要补充，请查看退回说明',
 '打开原事项查看当前进度与处理要求；已读不代表审批或签收。','material',NEW.request_id,NEW.created_by,NEW.created_at
 FROM material_requests WHERE id=NEW.request_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id AND assignee_user_id IS NOT NULL;
END;
CREATE TRIGGER IF NOT EXISTS client_notice_package AFTER INSERT ON monthly_delivery_packages
WHEN NOT EXISTS(SELECT 1 FROM restore_state WHERE hold<>0 OR hold IS NULL)
BEGIN
 INSERT OR IGNORE INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,body,related_kind,related_ref,created_by,created_at)
 VALUES('task:monthly_delivery_packages:'||NEW.id,NEW.firm_id,NEW.company_id,NEW.contact_user_id,'reminder','月度资料包已准备好，请核对并签收',
 '打开原事项查看当前进度与处理要求；已读不代表审批或签收。','monthly_package',NEW.id,NEW.created_by,NEW.created_at);
END;
