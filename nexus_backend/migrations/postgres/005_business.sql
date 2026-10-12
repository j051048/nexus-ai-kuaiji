-- Run as the migration administrator. Do not expose this schema through PostgREST.
ALTER TABLE accounting.sources ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'bank' CHECK(source_kind IN ('bank','business'));
CREATE UNIQUE INDEX idx_books_business_scope ON accounting.books(id,firm_id,company_id);
CREATE UNIQUE INDEX idx_drafts_business_source ON accounting.drafts(id,source_id,book_id);

CREATE TABLE accounting.department_approvers (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,department_id TEXT NOT NULL,user_id TEXT NOT NULL,
 PRIMARY KEY(firm_id,department_id,user_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),
 FOREIGN KEY(department_id,company_id) REFERENCES accounting.departments(id,company_id),
 FOREIGN KEY(company_id,user_id) REFERENCES accounting.company_memberships(company_id,user_id) ON DELETE CASCADE
);
CREATE TABLE accounting.business_events (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,department_id TEXT,
 applicant_id TEXT NOT NULL REFERENCES accounting.users(id),
 template_id TEXT NOT NULL CHECK(template_id IN ('reimbursement','procurement_expense','daily_cashflow')),
 template_version INTEGER NOT NULL DEFAULT 1 CHECK(template_version>0),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','department_approved','finance_approved','handed_off','returned')),
 payload TEXT NOT NULL CHECK(jsonb_typeof(payload::jsonb)='object'),
 submitted_version INTEGER CHECK(submitted_version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),
 FOREIGN KEY(department_id,company_id) REFERENCES accounting.departments(id,company_id)
);
CREATE TABLE accounting.business_versions (
 event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_version INTEGER NOT NULL CHECK(event_version>0),
 payload TEXT NOT NULL CHECK(jsonb_typeof(payload::jsonb)='object'),
 template_snapshot TEXT NOT NULL CHECK(jsonb_typeof(template_snapshot::jsonb)='object'),
 attachment_snapshot TEXT NOT NULL CHECK(jsonb_typeof(attachment_snapshot::jsonb)='array'),
 created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),
 PRIMARY KEY(event_id,event_version),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id)
);
CREATE TABLE accounting.business_attachments (
 id TEXT PRIMARY KEY,event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL,size_bytes BIGINT NOT NULL CHECK(size_bytes>0),
 created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),UNIQUE(id,event_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id)
);
CREATE TABLE accounting.business_actions (
 id TEXT PRIMARY KEY,event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 submitted_version INTEGER NOT NULL CHECK(submitted_version>0),
 stage TEXT NOT NULL CHECK(stage IN ('department','finance','handoff','return')),
 decision TEXT NOT NULL CHECK(decision IN ('approve','return','handoff')),reason TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(event_id,submitted_version) REFERENCES accounting.business_versions(event_id,event_version)
);
CREATE TABLE accounting.business_mappings (
 event_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 source_id TEXT NOT NULL,draft_id TEXT NOT NULL,submitted_version INTEGER NOT NULL CHECK(submitted_version>0),
 idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,UNIQUE(firm_id,idempotency_key),
 UNIQUE(source_id),UNIQUE(draft_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(event_id,submitted_version) REFERENCES accounting.business_versions(event_id,event_version),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES accounting.drafts(id,book_id),
 FOREIGN KEY(draft_id,source_id,book_id) REFERENCES accounting.drafts(id,source_id,book_id)
);
CREATE INDEX idx_business_event_company ON accounting.business_events(firm_id,company_id,status,updated_at);
CREATE INDEX idx_business_event_applicant ON accounting.business_events(firm_id,company_id,applicant_id);
CREATE INDEX idx_business_event_department ON accounting.business_events(firm_id,company_id,department_id,status);
CREATE INDEX idx_business_attachment_event ON accounting.business_attachments(event_id,created_at);
CREATE INDEX idx_business_action_event ON accounting.business_actions(event_id,created_at);
CREATE INDEX idx_business_mapping_book ON accounting.business_mappings(book_id,event_id);
CREATE INDEX idx_department_approver_user ON accounting.department_approvers(firm_id,user_id,company_id);

INSERT INTO accounting.accounts(book_id,code,name) SELECT id,'2241','其他应付款' FROM accounting.books ON CONFLICT(book_id,code) DO NOTHING;

CREATE FUNCTION accounting.business_member(target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients fc JOIN accounting.company_memberships cm ON cm.company_id=fc.company_id
  WHERE fc.firm_id=target_firm AND fc.company_id=target_company AND fc.status='active' AND cm.user_id=accounting.actor_user())
$$;
CREATE FUNCTION accounting.business_finance(target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_member(target_firm,target_company) AND EXISTS(
  SELECT 1 FROM accounting.company_memberships WHERE company_id=target_company AND user_id=accounting.actor_user() AND role='finance')
$$;
CREATE FUNCTION accounting.business_department_approver(target_firm TEXT,target_company TEXT,target_department TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_member(target_firm,target_company) AND EXISTS(
  SELECT 1 FROM accounting.department_approvers WHERE firm_id=target_firm AND company_id=target_company
  AND department_id=target_department AND user_id=accounting.actor_user())
$$;
CREATE FUNCTION accounting.business_row_visible(target_event TEXT,target_firm TEXT,target_company TEXT,target_department TEXT,target_applicant TEXT,target_status TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active') AND (
  (accounting.business_member(target_firm,target_company) AND target_applicant=accounting.actor_user())
  OR accounting.business_finance(target_firm,target_company)
  OR accounting.business_department_approver(target_firm,target_company,target_department)
  OR (target_status='handed_off' AND EXISTS(SELECT 1 FROM accounting.business_mappings m
   WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company AND accounting.book_allowed(m.book_id)))
 )
$$;
CREATE FUNCTION accounting.business_event_visible(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company
  AND accounting.business_row_visible(e.id,e.firm_id,e.company_id,e.department_id,e.applicant_id,e.status))
$$;
CREATE FUNCTION accounting.business_own_editable(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_member(target_firm,target_company) AND EXISTS(
  SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company
  AND e.applicant_id=accounting.actor_user() AND e.status IN ('draft','returned'))
$$;
CREATE FUNCTION accounting.business_approver_write(target_event TEXT,target_firm TEXT,target_company TEXT,allow_handoff BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company AND (
  (e.applicant_id<>accounting.actor_user() AND (accounting.business_finance(target_firm,target_company)
   OR accounting.business_department_approver(target_firm,target_company,e.department_id)))
  OR (allow_handoff AND accounting.business_finance(target_firm,target_company) AND e.status IN ('finance_approved','handed_off'))
 ))
$$;

CREATE FUNCTION accounting.business_event_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.department_id,NEW.applicant_id,NEW.template_id,NEW.template_version,NEW.created_at)
   IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.department_id,OLD.applicant_id,OLD.template_id,OLD.template_version,OLD.created_at) THEN
   RAISE EXCEPTION 'business event identity is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER business_events_identity BEFORE UPDATE ON accounting.business_events
 FOR EACH ROW EXECUTE FUNCTION accounting.business_event_identity_guard();

REVOKE ALL ON FUNCTION accounting.business_member(TEXT,TEXT),accounting.business_finance(TEXT,TEXT),
 accounting.business_department_approver(TEXT,TEXT,TEXT),accounting.business_row_visible(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
 accounting.business_event_visible(TEXT,TEXT,TEXT),accounting.business_own_editable(TEXT,TEXT,TEXT),
 accounting.business_approver_write(TEXT,TEXT,TEXT,BOOLEAN),accounting.business_event_identity_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.business_member(TEXT,TEXT),accounting.business_finance(TEXT,TEXT),
 accounting.business_department_approver(TEXT,TEXT,TEXT),accounting.business_row_visible(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
 accounting.business_event_visible(TEXT,TEXT,TEXT),accounting.business_own_editable(TEXT,TEXT,TEXT),
 accounting.business_approver_write(TEXT,TEXT,TEXT,BOOLEAN) TO accounting_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON accounting.department_approvers TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.business_events TO accounting_app;
GRANT SELECT,INSERT ON accounting.business_versions,accounting.business_attachments,accounting.business_actions,accounting.business_mappings TO accounting_app;
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['department_approvers','business_events','business_versions','business_attachments','business_actions','business_mappings'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 END LOOP;
END $$;
CREATE POLICY approvers_read ON accounting.department_approvers FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND ((user_id=accounting.actor_user() AND accounting.business_member(firm_id,company_id)) OR accounting.company_manager(company_id)));
CREATE POLICY approvers_create ON accounting.department_approvers FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id));
CREATE POLICY approvers_update ON accounting.department_approvers FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id));
CREATE POLICY approvers_delete ON accounting.department_approvers FOR DELETE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id));
CREATE POLICY business_events_read ON accounting.business_events FOR SELECT TO accounting_app
 USING(accounting.business_row_visible(id,firm_id,company_id,department_id,applicant_id,status));
CREATE POLICY business_events_create ON accounting.business_events FOR INSERT TO accounting_app
 WITH CHECK(accounting.business_member(firm_id,company_id) AND applicant_id=accounting.actor_user() AND status='draft' AND version=1 AND submitted_version IS NULL);
CREATE POLICY business_events_update ON accounting.business_events FOR UPDATE TO accounting_app
 USING(accounting.business_own_editable(id,firm_id,company_id) OR accounting.business_approver_write(id,firm_id,company_id,true))
 WITH CHECK(accounting.business_event_visible(id,firm_id,company_id));
CREATE POLICY business_versions_read ON accounting.business_versions FOR SELECT TO accounting_app
 USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY business_versions_create ON accounting.business_versions FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.business_own_editable(event_id,firm_id,company_id));
CREATE POLICY business_attachments_read ON accounting.business_attachments FOR SELECT TO accounting_app
 USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY business_attachments_create ON accounting.business_attachments FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.business_own_editable(event_id,firm_id,company_id));
CREATE POLICY business_actions_read ON accounting.business_actions FOR SELECT TO accounting_app
 USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY business_actions_create ON accounting.business_actions FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.business_event_visible(event_id,firm_id,company_id)
  AND accounting.business_approver_write(event_id,firm_id,company_id,stage='handoff' AND decision='handoff'));
CREATE POLICY business_mappings_read ON accounting.business_mappings FOR SELECT TO accounting_app
 USING(accounting.business_finance(firm_id,company_id) OR (firm_id=accounting.actor_firm()
  AND accounting.book_allowed(book_id) AND accounting.business_event_visible(event_id,firm_id,company_id)));
CREATE POLICY business_mappings_create ON accounting.business_mappings FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.business_finance(firm_id,company_id)
  AND accounting.book_allowed(book_id,true) AND accounting.business_event_visible(event_id,firm_id,company_id));
