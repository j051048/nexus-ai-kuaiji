-- Monthly client work ownership and explicit material requests. Never grants
-- book access, creates accounting facts, or approves/exports a voucher.
CREATE TABLE accounting.service_plan_versions(
 book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),
 lead_user_id TEXT REFERENCES accounting.users(id),reviewer_user_id TEXT REFERENCES accounting.users(id),backup_user_id TEXT REFERENCES accounting.users(id),contact_user_id TEXT REFERENCES accounting.users(id),
 due_on TEXT NOT NULL,notes TEXT NOT NULL CHECK(length(notes)<=2000),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),updated_by TEXT NOT NULL REFERENCES accounting.users(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(book_id,version),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),CHECK(lead_user_id IS NULL OR reviewer_user_id IS NULL OR lead_user_id<>reviewer_user_id)
);
CREATE TABLE accounting.material_templates(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,ledger_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version=1),
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),items_json TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
CREATE TABLE accounting.material_requests(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,period TEXT NOT NULL,template_id TEXT NOT NULL,item_key TEXT NOT NULL,
 title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),description TEXT NOT NULL CHECK(length(description)<=1000),required INTEGER NOT NULL CHECK(required IN(0,1)),due_on TEXT NOT NULL,
 assignee_user_id TEXT REFERENCES accounting.users(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(book_id,template_id,item_key),UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(template_id,firm_id,company_id) REFERENCES accounting.material_templates(id,firm_id,company_id)
);
CREATE TABLE accounting.material_request_actions(
 id TEXT PRIMARY KEY,request_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>1),
 action TEXT NOT NULL CHECK(action IN('submit','accept','return')),state TEXT NOT NULL CHECK(state IN('submitted','accepted','returned')),mode TEXT CHECK(mode IN('provided','no_activity','not_applicable')),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),event_ids_json TEXT NOT NULL,evidence_json TEXT NOT NULL,attachment_ids_json TEXT NOT NULL DEFAULT '[]',attachment_evidence_json TEXT NOT NULL DEFAULT '[]',created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(request_id,version),FOREIGN KEY(request_id,book_id,firm_id,company_id) REFERENCES accounting.material_requests(id,book_id,firm_id,company_id),
 CHECK((action='submit' AND state='submitted' AND mode IS NOT NULL) OR (action='accept' AND state='accepted' AND mode IS NULL) OR (action='return' AND state='returned' AND mode IS NULL))
);
CREATE INDEX idx_service_plans_company ON accounting.service_plan_versions(firm_id,company_id,book_id,version DESC);
CREATE INDEX idx_material_templates_ledger ON accounting.material_templates(ledger_id,created_at,id);
CREATE INDEX idx_material_requests_assignee ON accounting.material_requests(firm_id,assignee_user_id,period,due_on,id);
CREATE INDEX idx_material_requests_book ON accounting.material_requests(book_id,due_on,id);

CREATE TABLE accounting.material_attachments(
 id TEXT PRIMARY KEY,request_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE,filename TEXT NOT NULL CHECK(length(trim(filename)) BETWEEN 1 AND 150),media_type TEXT NOT NULL CHECK(media_type IN('application/pdf','image/png','image/jpeg','image/webp','application/xml','text/csv','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
 sha256 TEXT NOT NULL CHECK(sha256~'^[a-f0-9]{64}$'),size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(request_id,book_id,firm_id,company_id) REFERENCES accounting.material_requests(id,book_id,firm_id,company_id)
);
CREATE INDEX idx_material_attachments_request ON accounting.material_attachments(request_id,created_at,id);

CREATE FUNCTION accounting.service_book_user(target TEXT,person TEXT,capability TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id AND fc.status='active'
 JOIN accounting.users u ON u.id=person AND u.active=1
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=person
 LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=person
 LEFT JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=person
 WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND CASE capability
 WHEN 'contact' THEN cm.user_id IS NOT NULL
 WHEN 'review' THEN m.role IN('owner','admin') OR ba.role='reviewer' AND (m.role='reviewer' OR m.role IS NULL AND cm.role='finance')
 WHEN 'edit' THEN m.role IN('owner','admin') OR ba.role IN('editor','reviewer') AND (m.role IN('operator','reviewer') OR m.role IS NULL AND cm.role IN('owner','admin','finance'))
 ELSE false END)
$$;
CREATE FUNCTION accounting.service_book_staff(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND accounting.service_book_user(target,accounting.actor_user(),'edit')
$$;
CREATE FUNCTION accounting.service_plan_candidates(target TEXT) RETURNS TABLE(user_id TEXT,name TEXT,can_lead BOOLEAN,can_review BOOLEAN,can_contact BOOLEAN) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT u.id,u.name,accounting.service_book_user(target,u.id,'edit'),accounting.service_book_user(target,u.id,'review'),accounting.service_book_user(target,u.id,'contact')
 FROM accounting.users u WHERE u.active=1 AND accounting.service_book_staff(target)
 AND u.id IN(SELECT m.user_id FROM accounting.memberships m WHERE m.firm_id=accounting.actor_firm()
 UNION SELECT cm.user_id FROM accounting.company_memberships cm JOIN accounting.books b ON b.company_id=cm.company_id WHERE b.id=target AND b.firm_id=accounting.actor_firm()
 UNION SELECT ba.user_id FROM accounting.book_access ba WHERE ba.book_id=target AND ba.firm_id=accounting.actor_firm())
 AND (accounting.service_book_user(target,u.id,'edit') OR accounting.service_book_user(target,u.id,'contact')) ORDER BY u.name,u.id
$$;
CREATE FUNCTION accounting.service_plan_labels(target TEXT) RETURNS TABLE(lead_name TEXT,reviewer_name TEXT) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT lead.name,reviewer.name FROM accounting.service_plan_versions p LEFT JOIN accounting.users lead ON lead.id=p.lead_user_id LEFT JOIN accounting.users reviewer ON reviewer.id=p.reviewer_user_id
 WHERE p.book_id=target AND accounting.service_book_staff(target) ORDER BY p.version DESC LIMIT 1
$$;
CREATE FUNCTION accounting.service_material_visible(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(SELECT 1 FROM accounting.material_requests r
 JOIN accounting.companies c ON c.id=r.company_id AND c.status='active' JOIN accounting.firm_clients fc ON fc.firm_id=r.firm_id AND fc.company_id=r.company_id AND fc.status='active'
 WHERE r.id=target AND r.firm_id=accounting.actor_firm() AND (accounting.service_book_staff(r.book_id) OR r.assignee_user_id=accounting.actor_user() AND accounting.service_book_user(r.book_id,accounting.actor_user(),'contact')))
$$;
CREATE FUNCTION accounting.service_material_open(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.service_material_visible(target) AND EXISTS(SELECT 1 FROM accounting.material_requests r JOIN accounting.periods p ON p.book_id=r.book_id AND p.period=r.period WHERE r.id=target AND p.status='open')
$$;
CREATE FUNCTION accounting.service_material_file_visible(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.material_attachments a JOIN accounting.material_requests r ON r.id=a.request_id WHERE a.id=target AND accounting.service_material_visible(r.id)
 AND (r.assignee_user_id=accounting.actor_user() AND accounting.service_book_user(r.book_id,accounting.actor_user(),'contact') OR accounting.service_book_staff(r.book_id)
 AND a.id IN(SELECT jsonb_array_elements_text(coalesce((SELECT s.attachment_ids_json FROM accounting.material_request_actions s WHERE s.request_id=r.id AND s.action='submit' ORDER BY s.version DESC LIMIT 1),'[]')::jsonb))))
$$;
CREATE FUNCTION accounting.service_material_files(target TEXT,ids JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE result JSONB:='[]';item JSONB;a RECORD;BEGIN
 IF NOT accounting.service_material_visible(target) OR jsonb_typeof(ids)<>'array' OR jsonb_array_length(ids)>30 OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(ids)) THEN RETURN NULL;END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(ids) LOOP
  IF jsonb_typeof(item)<>'string' THEN RETURN NULL;END IF;
  SELECT * INTO a FROM accounting.material_attachments WHERE id=item#>>'{}' AND request_id=target;
  IF NOT FOUND OR NOT accounting.service_material_file_visible(a.id) THEN RETURN NULL;END IF;
  result:=result||jsonb_build_array(jsonb_build_object('id',a.id,'sha256',a.sha256));
 END LOOP;RETURN result;END $$;
-- Snapshots retain IDs, factual versions and original hashes only. Descriptions
-- and filenames are resolved through business_event_visible at read time.
CREATE FUNCTION accounting.service_material_evidence(target TEXT,event_ids JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE r RECORD;e RECORD;item JSONB;result JSONB:='[]';attachments JSONB;BEGIN
 SELECT * INTO r FROM accounting.material_requests WHERE id=target;
 IF NOT FOUND OR NOT accounting.service_material_visible(target) OR jsonb_typeof(event_ids)<>'array' OR jsonb_array_length(event_ids)>30 OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(event_ids)) THEN RETURN NULL;END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(event_ids) LOOP
  IF jsonb_typeof(item)<>'string' THEN RETURN NULL;END IF;
  SELECT * INTO e FROM accounting.business_events WHERE id=item#>>'{}' AND firm_id=r.firm_id AND company_id=r.company_id AND applicant_id=r.assignee_user_id AND left(payload::jsonb->>'occurred_on',7)=r.period AND status<>'draft';
  IF NOT FOUND OR NOT accounting.business_event_visible(e.id,e.firm_id,e.company_id) THEN RETURN NULL;END IF;
  SELECT jsonb_agg(jsonb_build_object('id',a.id,'sha256',a.sha256) ORDER BY a.created_at,a.id) INTO attachments FROM accounting.business_attachments a WHERE a.event_id=e.id AND NOT EXISTS(SELECT 1 FROM accounting.case_supersessions z WHERE z.event_id=e.id AND z.old_attachment_id=a.id);
  IF attachments IS NULL THEN RETURN NULL;END IF;
  result:=result||jsonb_build_array(jsonb_build_object('event_id',e.id,'event_version',coalesce(e.submitted_version,e.version),'fact_version',e.fact_version,'attachments',attachments));
 END LOOP;
 RETURN result;
END $$;
CREATE FUNCTION accounting.service_plans_native_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE b RECORD;t RECORD;r RECORD;prior RECORD;submission RECORD;item JSONB;expected JSONB;BEGIN
 SELECT bk.*,p.status,lp.ledger_id INTO b FROM accounting.books bk JOIN accounting.periods p ON p.book_id=bk.id AND p.period=bk.period JOIN accounting.ledger_periods lp ON lp.book_id=bk.id
 JOIN accounting.companies c ON c.id=bk.company_id AND c.status='active' JOIN accounting.firm_clients fc ON fc.firm_id=bk.firm_id AND fc.company_id=bk.company_id AND fc.status='active'
 WHERE bk.id=NEW.book_id AND bk.firm_id=NEW.firm_id AND bk.company_id=NEW.company_id;
 IF NOT FOUND OR b.status<>'open' OR NEW.firm_id<>accounting.actor_firm() OR NOT accounting.actor_active() THEN RAISE EXCEPTION 'Current active same-company open period required' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME='service_plan_versions' THEN
  IF NOT accounting.service_book_staff(NEW.book_id) OR NEW.updated_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Current book editor required' USING ERRCODE='42501';END IF;
  IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM accounting.service_plan_versions WHERE book_id=NEW.book_id) OR NEW.due_on!~'^20[0-9]{2}-[0-9]{2}-[0-9]{2}$' OR NEW.due_on::date::text<>NEW.due_on THEN RAISE EXCEPTION 'Consecutive plan version and valid date required' USING ERRCODE='23514';END IF;
  IF NEW.lead_user_id IS NOT NULL AND NOT accounting.service_book_user(NEW.book_id,NEW.lead_user_id,'edit') OR NEW.reviewer_user_id IS NOT NULL AND NOT accounting.service_book_user(NEW.book_id,NEW.reviewer_user_id,'review') OR NEW.backup_user_id IS NOT NULL AND NOT accounting.service_book_user(NEW.book_id,NEW.backup_user_id,'edit') OR NEW.contact_user_id IS NOT NULL AND NOT accounting.service_book_user(NEW.book_id,NEW.contact_user_id,'contact') THEN RAISE EXCEPTION 'Assignments cannot create or retain revoked permissions' USING ERRCODE='42501';END IF;
 ELSIF TG_TABLE_NAME='material_templates' THEN
  IF NOT accounting.service_book_staff(NEW.book_id) OR NEW.created_by<>accounting.actor_user() OR NEW.ledger_id<>b.ledger_id THEN RAISE EXCEPTION 'Current same-ledger book editor required' USING ERRCODE='42501';END IF;
  IF jsonb_typeof(NEW.items_json::jsonb)<>'array' OR jsonb_array_length(NEW.items_json::jsonb) NOT BETWEEN 1 AND 30 OR (SELECT count(*)<>count(DISTINCT value->>'key') FROM jsonb_array_elements(NEW.items_json::jsonb)) THEN RAISE EXCEPTION 'Unique material template items required' USING ERRCODE='23514';END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(NEW.items_json::jsonb) LOOP
   IF jsonb_typeof(item->'key') IS DISTINCT FROM 'string' OR jsonb_typeof(item->'title') IS DISTINCT FROM 'string' OR jsonb_typeof(item->'description') IS DISTINCT FROM 'string' OR jsonb_typeof(item->'due_month_offset') IS DISTINCT FROM 'number' OR item->>'due_month_offset' NOT IN('0','1') OR item->>'key'!~'^[a-zA-Z0-9_-]{1,60}$' OR length(trim(item->>'title')) NOT BETWEEN 1 AND 120 OR length(item->>'description')>1000 OR jsonb_typeof(item->'required') IS DISTINCT FROM 'boolean' OR jsonb_typeof(item->'due_day') IS DISTINCT FROM 'number' OR item->>'due_day'!~'^[0-9]+$' OR (item->>'due_day')::integer NOT BETWEEN 1 AND 28 OR NOT(item ? 'assignee_user_id') THEN RAISE EXCEPTION 'Invalid material template item' USING ERRCODE='23514';END IF;
   IF item->>'assignee_user_id' IS NOT NULL AND NOT accounting.service_book_user(NEW.book_id,item->>'assignee_user_id','contact') THEN RAISE EXCEPTION 'Current company member required for material assignment' USING ERRCODE='42501';END IF;
  END LOOP;
 ELSIF TG_TABLE_NAME='material_requests' THEN
  IF NOT accounting.service_book_staff(NEW.book_id) OR NEW.created_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Current book editor required' USING ERRCODE='42501';END IF;
  SELECT * INTO t FROM accounting.material_templates WHERE id=NEW.template_id AND ledger_id=b.ledger_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Same-ledger template required' USING ERRCODE='23514';END IF;
  SELECT value INTO item FROM jsonb_array_elements(t.items_json::jsonb) WHERE value->>'key'=NEW.item_key;
  IF item IS NULL OR NEW.period<>b.period OR NEW.title IS DISTINCT FROM item->>'title' OR NEW.description IS DISTINCT FROM item->>'description' OR NEW.required<>(CASE WHEN (item->>'required')::boolean THEN 1 ELSE 0 END) OR NEW.due_on<>to_char(to_date(b.period||'-01','YYYY-MM-DD')+(item->>'due_month_offset')::integer*interval '1 month','YYYY-MM')||'-'||lpad(item->>'due_day',2,'0') OR NEW.assignee_user_id IS DISTINCT FROM coalesce(item->>'assignee_user_id',(SELECT contact_user_id FROM accounting.service_plan_versions WHERE book_id=NEW.book_id ORDER BY version DESC LIMIT 1)) OR NEW.assignee_user_id IS NULL THEN RAISE EXCEPTION 'Generated material must equal explicit template' USING ERRCODE='23514';END IF;
  IF NEW.assignee_user_id IS NOT NULL AND NOT accounting.service_book_user(NEW.book_id,NEW.assignee_user_id,'contact') THEN RAISE EXCEPTION 'Material assignment requires current company membership' USING ERRCODE='42501';END IF;
 ELSIF TG_TABLE_NAME='material_attachments' THEN
  SELECT * INTO r FROM accounting.material_requests WHERE id=NEW.request_id AND book_id=NEW.book_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id;
  IF NOT FOUND OR r.assignee_user_id IS DISTINCT FROM accounting.actor_user() OR NEW.created_by<>accounting.actor_user() OR NOT accounting.service_book_user(r.book_id,accounting.actor_user(),'contact') THEN RAISE EXCEPTION 'Only current assigned company member uploads' USING ERRCODE='42501';END IF;
  SELECT * INTO prior FROM accounting.material_request_actions WHERE request_id=r.id ORDER BY version DESC LIMIT 1;
  IF coalesce(prior.state,'pending') NOT IN('pending','returned') OR (SELECT count(*) FROM accounting.material_attachments WHERE request_id=r.id)>=10 OR (SELECT coalesce(sum(size_bytes),0) FROM accounting.material_attachments WHERE request_id=r.id)+NEW.size_bytes>52428800 THEN RAISE EXCEPTION 'Editable request with bounded original attachments required' USING ERRCODE='23514';END IF;
 ELSE
  IF NEW.created_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Action attribution must match current actor' USING ERRCODE='42501';END IF;
  SELECT * INTO r FROM accounting.material_requests WHERE id=NEW.request_id AND book_id=NEW.book_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Same-scope material request required' USING ERRCODE='23514';END IF;
  SELECT * INTO prior FROM accounting.material_request_actions WHERE request_id=r.id ORDER BY version DESC LIMIT 1;
  IF NEW.version<>coalesce(prior.version,1)+1 THEN RAISE EXCEPTION 'Consecutive material version required' USING ERRCODE='23514';END IF;
  IF NEW.action='submit' THEN
   IF r.assignee_user_id IS DISTINCT FROM accounting.actor_user() OR NOT accounting.service_book_user(r.book_id,accounting.actor_user(),'contact') THEN RAISE EXCEPTION 'Only current assigned company member submits' USING ERRCODE='42501';END IF;
   IF coalesce(prior.state,'pending') NOT IN('pending','returned') THEN RAISE EXCEPTION 'Submitted material requires independent review' USING ERRCODE='23514';END IF;
   expected:=accounting.service_material_evidence(r.id,NEW.event_ids_json::jsonb);
   IF expected IS NULL OR NEW.evidence_json::jsonb IS DISTINCT FROM expected OR (NEW.mode='provided')<>(jsonb_array_length(NEW.event_ids_json::jsonb)+jsonb_array_length(NEW.attachment_ids_json::jsonb)>0) OR NEW.attachment_evidence_json::jsonb IS DISTINCT FROM accounting.service_material_files(r.id,NEW.attachment_ids_json::jsonb) THEN RAISE EXCEPTION 'Current own month evidence and original hashes required' USING ERRCODE='23514';END IF;
  ELSE
   IF NOT accounting.ai_book_reviewer(r.book_id) THEN RAISE EXCEPTION 'Current scoped book reviewer required' USING ERRCODE='42501';END IF;
   SELECT * INTO submission FROM accounting.material_request_actions WHERE request_id=r.id AND action='submit' ORDER BY version DESC LIMIT 1;
   IF prior.state IS NULL OR prior.state NOT IN('submitted','accepted') OR prior.state='accepted' AND NEW.action<>'return' OR submission.created_by=NEW.created_by THEN RAISE EXCEPTION 'Independent review or return of accepted material required' USING ERRCODE='23514';END IF;
   IF NEW.event_ids_json::jsonb<>'[]'::jsonb OR NEW.evidence_json::jsonb<>'[]'::jsonb OR NEW.attachment_ids_json::jsonb<>'[]'::jsonb OR NEW.attachment_evidence_json::jsonb<>'[]'::jsonb THEN RAISE EXCEPTION 'Review cannot replace submitted evidence' USING ERRCODE='23514';END IF;
   IF NEW.action='accept' AND (accounting.service_material_evidence(r.id,prior.event_ids_json::jsonb) IS DISTINCT FROM prior.evidence_json::jsonb OR accounting.service_material_files(r.id,prior.attachment_ids_json::jsonb) IS DISTINCT FROM prior.attachment_evidence_json::jsonb) THEN RAISE EXCEPTION 'Evidence changed or permission revoked; re-submit required' USING ERRCODE='23514';END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

DO $$ DECLARE tab TEXT;scope TEXT;BEGIN
 FOREACH tab IN ARRAY ARRAY['service_plan_versions','material_templates','material_requests','material_request_actions','material_attachments'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',tab);EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',tab);EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',tab);
  scope:=CASE WHEN tab='material_requests' THEN 'accounting.service_material_visible(id)' WHEN tab='material_request_actions' THEN 'accounting.service_material_visible(request_id)' WHEN tab='material_attachments' THEN 'accounting.service_material_file_visible(id)' WHEN tab='material_templates' THEN 'EXISTS(SELECT 1 FROM accounting.ledger_periods lp WHERE lp.ledger_id=material_templates.ledger_id AND accounting.service_book_staff(lp.book_id))' ELSE 'accounting.service_book_staff(book_id)' END;
  EXECUTE format('CREATE POLICY service_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND %s)',tab,scope);
  scope:=CASE WHEN tab IN('material_request_actions','material_attachments') THEN 'accounting.service_material_visible(request_id)' ELSE 'accounting.service_book_staff(book_id)' END;
  EXECUTE format('CREATE POLICY service_insert ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND %s)',tab,scope);
  EXECUTE format('CREATE TRIGGER service_native BEFORE INSERT ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.service_plans_native_guard()',tab);
  EXECUTE format('CREATE TRIGGER service_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',tab);
  EXECUTE format('CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard()',tab);
  EXECUTE format('CREATE TRIGGER batch2_statement_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate()',tab);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION accounting.service_book_user(TEXT,TEXT,TEXT),accounting.service_book_staff(TEXT),accounting.service_plan_candidates(TEXT),accounting.service_material_visible(TEXT),accounting.service_material_open(TEXT),accounting.service_material_evidence(TEXT,JSONB),accounting.service_material_file_visible(TEXT),accounting.service_material_files(TEXT,JSONB),accounting.service_plans_native_guard() FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.service_book_staff(TEXT),accounting.service_plan_candidates(TEXT),accounting.service_material_visible(TEXT),accounting.service_material_open(TEXT),accounting.service_material_file_visible(TEXT) TO accounting_app;
REVOKE ALL ON FUNCTION accounting.service_plan_labels(TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.service_plan_labels(TEXT) TO accounting_app;

CREATE FUNCTION accounting.service_material_audit() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_TABLE_NAME='material_attachments' THEN
 INSERT INTO accounting.audit_events(firm_id,book_id,company_id,user_id,action,object_id,details,created_at)
 VALUES(NEW.firm_id,NEW.book_id,NEW.company_id,NEW.created_by,'material.attachment.upload',NEW.request_id,jsonb_build_object('attachment_id',NEW.id,'sha256',NEW.sha256,'size_bytes',NEW.size_bytes)::text,NEW.created_at);
 RETURN NEW;END IF;
 INSERT INTO accounting.audit_events(firm_id,book_id,company_id,user_id,action,object_id,details,created_at)
 VALUES(NEW.firm_id,NEW.book_id,NEW.company_id,NEW.created_by,'material.'||NEW.action,NEW.request_id,jsonb_build_object('reason',NEW.reason,'version',NEW.version,'mode',NEW.mode)::text,NEW.created_at);
 RETURN NEW;END $$;
REVOKE ALL ON FUNCTION accounting.service_material_audit() FROM PUBLIC,accounting_app,accounting_worker;
CREATE TRIGGER service_material_audit AFTER INSERT ON accounting.material_request_actions FOR EACH ROW EXECUTE FUNCTION accounting.service_material_audit();
CREATE TRIGGER service_material_attachment_audit AFTER INSERT ON accounting.material_attachments FOR EACH ROW EXECUTE FUNCTION accounting.service_material_audit();

CREATE FUNCTION accounting.service_material_current(target TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE latest RECORD;submitted RECORD;BEGIN
 IF NOT accounting.service_material_visible(target) THEN RETURN false;END IF;
 SELECT * INTO latest FROM accounting.material_request_actions WHERE request_id=target ORDER BY version DESC LIMIT 1;
 IF NOT FOUND OR latest.state<>'accepted' THEN RETURN false;END IF;
 SELECT * INTO submitted FROM accounting.material_request_actions WHERE request_id=target AND action='submit' ORDER BY version DESC LIMIT 1;
 RETURN accounting.service_material_evidence(target,submitted.event_ids_json::jsonb) IS NOT DISTINCT FROM submitted.evidence_json::jsonb AND accounting.service_material_files(target,submitted.attachment_ids_json::jsonb) IS NOT DISTINCT FROM submitted.attachment_evidence_json::jsonb;
END $$;
REVOKE ALL ON FUNCTION accounting.service_material_current(TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.service_material_current(TEXT) TO accounting_app;

-- The native close path calls these same functions. A passed old snapshot
-- cannot silently survive a new requirement, a submission, or a changed fact.
ALTER FUNCTION accounting.closing_fingerprint(TEXT) RENAME TO closing_fingerprint_stage24;
CREATE FUNCTION accounting.closing_fingerprint(target_book TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE extra JSONB;old_hash TEXT;BEGIN
 old_hash:=accounting.closing_fingerprint_stage24(target_book);
 SELECT jsonb_build_object(
 'service_plan_versions',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.version),'[]') FROM accounting.service_plan_versions t WHERE t.book_id=target_book),
 'material_requests',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]') FROM accounting.material_requests t WHERE t.book_id=target_book),
 'material_request_actions',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.request_id,t.version),'[]') FROM accounting.material_request_actions t WHERE t.book_id=target_book),
 'material_attachments',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]') FROM accounting.material_attachments t WHERE t.book_id=target_book)) INTO extra;
 RETURN encode(sha256(convert_to(old_hash||extra::text,'UTF8')),'hex');END $$;
ALTER FUNCTION accounting.closing_issues(TEXT) RENAME TO closing_issues_stage24;
CREATE FUNCTION accounting.closing_issues(target_book TEXT) RETURNS TABLE(category TEXT,object_type TEXT,object_id TEXT,detail TEXT) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current book reviewer required' USING ERRCODE='42501';END IF;
 RETURN QUERY SELECT * FROM accounting.closing_issues_stage24(target_book)
 UNION ALL SELECT 'required_material','material_request',r.id,'本月必交资料尚未独立验收，或已验收证据发生变化，请核对资料清单'
 FROM accounting.material_requests r WHERE r.book_id=target_book AND r.required=1 AND NOT accounting.service_material_current(r.id);
 END $$;
REVOKE ALL ON FUNCTION accounting.closing_fingerprint_stage24(TEXT),accounting.closing_issues_stage24(TEXT),accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT) TO accounting_app;



