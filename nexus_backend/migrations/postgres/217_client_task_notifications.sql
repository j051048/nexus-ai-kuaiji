-- compatibility: incremental
-- Fixed server-derived in-app notices only. No outbound jobs or automatic sends.
CREATE OR REPLACE FUNCTION accounting.client_task_notification() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE recipient TEXT; ref TEXT; source_kind TEXT; subject_text TEXT; notice_kind TEXT := 'reminder';
BEGIN
 IF NOT accounting.actor_active() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
 OR EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold IS DISTINCT FROM FALSE)
 THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='business_actions' THEN
  IF NEW.decision<>'return' THEN RETURN NEW; END IF;
  SELECT applicant_id INTO recipient FROM accounting.business_events
   WHERE id=NEW.event_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id;
  ref:=NEW.event_id; source_kind:='business'; subject_text:='事项已退回，请补充后重新提交'; notice_kind:='return';
 ELSIF TG_TABLE_NAME='material_requests' THEN
  recipient:=NEW.assignee_user_id; ref:=NEW.id; source_kind:='material'; subject_text:='有一项资料需要你提供';
 ELSIF TG_TABLE_NAME='material_request_actions' THEN
  IF NEW.action<>'return' THEN RETURN NEW; END IF;
  SELECT assignee_user_id INTO recipient FROM accounting.material_requests
   WHERE id=NEW.request_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id;
  ref:=NEW.request_id; source_kind:='material'; subject_text:='资料需要补充，请查看退回说明'; notice_kind:='return';
 ELSIF TG_TABLE_NAME='monthly_delivery_packages' THEN
  recipient:=NEW.contact_user_id; ref:=NEW.id; source_kind:='monthly_package'; subject_text:='月度资料包已准备好，请核对并签收';
 ELSE RAISE EXCEPTION 'Unsupported notification source' USING ERRCODE='42501'; END IF;
 IF recipient IS NULL THEN RETURN NEW; END IF;
 INSERT INTO accounting.client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,body,related_kind,related_ref,created_by,created_at)
 VALUES('task:'||TG_TABLE_NAME||':'||NEW.id,NEW.firm_id,NEW.company_id,recipient,notice_kind,subject_text,
 '打开原事项查看当前进度与处理要求；已读不代表审批或签收。',source_kind,ref,NEW.created_by,NEW.created_at)
 ON CONFLICT(id) DO NOTHING;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.client_task_notification() FROM PUBLIC,accounting_app,accounting_worker;
DROP TRIGGER IF EXISTS client_notice_return ON accounting.business_actions;
CREATE TRIGGER client_notice_return AFTER INSERT ON accounting.business_actions FOR EACH ROW EXECUTE FUNCTION accounting.client_task_notification();
DROP TRIGGER IF EXISTS client_notice_material ON accounting.material_requests;
CREATE TRIGGER client_notice_material AFTER INSERT ON accounting.material_requests FOR EACH ROW EXECUTE FUNCTION accounting.client_task_notification();
DROP TRIGGER IF EXISTS client_notice_material_return ON accounting.material_request_actions;
CREATE TRIGGER client_notice_material_return AFTER INSERT ON accounting.material_request_actions FOR EACH ROW EXECUTE FUNCTION accounting.client_task_notification();
DROP TRIGGER IF EXISTS client_notice_package ON accounting.monthly_delivery_packages;
CREATE TRIGGER client_notice_package AFTER INSERT ON accounting.monthly_delivery_packages FOR EACH ROW EXECUTE FUNCTION accounting.client_task_notification();
