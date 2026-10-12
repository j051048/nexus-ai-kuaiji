-- Model output is evidence and a proposal; it never grants ledger approval.
CREATE TABLE accounting.ai_company_settings (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 updated_by TEXT NOT NULL REFERENCES accounting.users(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
CREATE TABLE accounting.ai_runs (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('ocr','proposal')),attachment_id TEXT,draft_id TEXT,book_id TEXT,
 input_version INTEGER NOT NULL CHECK(input_version>0),input_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','stale')),
 result_json TEXT CHECK(result_json IS NULL OR jsonb_typeof(result_json::jsonb)='object'),
 provider TEXT NOT NULL,model TEXT NOT NULL,prompt_version TEXT NOT NULL,
 usage_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(usage_json::jsonb)='object'),
 error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(firm_id,event_id,kind,idempotency_key),UNIQUE(id,event_id,firm_id,company_id),
 UNIQUE(id,event_id,firm_id,company_id,book_id,draft_id),
 CHECK((kind='ocr' AND attachment_id IS NOT NULL AND book_id IS NULL AND draft_id IS NULL)
  OR (kind='proposal' AND attachment_id IS NULL AND book_id IS NOT NULL AND draft_id IS NOT NULL)),
 CHECK((status='running' AND result_json IS NULL AND finished_at IS NULL)
  OR (status='succeeded' AND result_json IS NOT NULL AND finished_at IS NOT NULL)
  OR (status IN ('failed','stale') AND result_json IS NULL AND finished_at IS NOT NULL)),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(attachment_id,event_id) REFERENCES accounting.business_attachments(id,event_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES accounting.drafts(id,book_id)
);
CREATE TABLE accounting.ai_findings (
 id TEXT PRIMARY KEY,run_id TEXT,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 code TEXT NOT NULL,severity TEXT NOT NULL CHECK(severity IN ('warning','blocking')),
 payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb)='object'),fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(event_id,code,fingerprint),UNIQUE(id,event_id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(run_id,event_id,firm_id,company_id) REFERENCES accounting.ai_runs(id,event_id,firm_id,company_id)
);
CREATE TABLE accounting.ai_finding_reviews (
 id TEXT PRIMARY KEY,finding_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(btrim(reason))>0),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(finding_id,event_id,firm_id,company_id) REFERENCES accounting.ai_findings(id,event_id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id)
);
CREATE UNIQUE INDEX idx_business_mapping_ai_scope ON accounting.business_mappings(event_id,firm_id,company_id,book_id,draft_id);
CREATE UNIQUE INDEX idx_business_mapping_ai_book_scope ON accounting.business_mappings(event_id,firm_id,company_id,book_id);
CREATE TABLE accounting.ai_proposal_applications (
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 book_id TEXT NOT NULL,draft_id TEXT NOT NULL,applied_draft_version INTEGER NOT NULL CHECK(applied_draft_version>0),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.business_mappings(event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES accounting.drafts(id,book_id)
);
CREATE TABLE accounting.ai_bank_links (
 bank_source_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,book_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,firm_id,company_id,book_id) REFERENCES accounting.business_mappings(event_id,firm_id,company_id,book_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(bank_source_id,book_id) REFERENCES accounting.sources(id,book_id)
);
CREATE INDEX idx_ai_runs_event ON accounting.ai_runs(event_id,kind,created_at);
CREATE INDEX idx_ai_runs_company ON accounting.ai_runs(firm_id,company_id,status,created_at);
CREATE INDEX idx_ai_findings_event ON accounting.ai_findings(event_id,created_at);
CREATE INDEX idx_ai_reviews_finding ON accounting.ai_finding_reviews(finding_id,created_at);
CREATE INDEX idx_ai_bank_links_event ON accounting.ai_bank_links(event_id,book_id);

CREATE FUNCTION accounting.ai_company_visible(target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients fc WHERE fc.firm_id=target_firm AND fc.company_id=target_company AND fc.status='active')
 AND (accounting.business_member(target_firm,target_company) OR accounting.firm_manager(target_firm)
  OR EXISTS(SELECT 1 FROM accounting.books b WHERE b.firm_id=target_firm AND b.company_id=target_company AND accounting.book_allowed(b.id)))
$$;
CREATE FUNCTION accounting.ai_book_reviewer(target_book TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book,true) AND (accounting.firm_manager(accounting.actor_firm()) OR EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id
  WHERE b.id=target_book AND ba.user_id=accounting.actor_user() AND ba.role='reviewer'
  AND (accounting.firm_role(b.firm_id)='reviewer' OR (accounting.firm_role(b.firm_id) IS NULL AND EXISTS(
   SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=b.company_id AND cm.user_id=accounting.actor_user() AND cm.role='finance')))))
$$;
CREATE FUNCTION accounting.ai_event_processor(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_event_visible(target_event,target_firm,target_company) AND EXISTS(
  SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company AND (
   (e.applicant_id=accounting.actor_user() AND accounting.business_member(target_firm,target_company))
   OR accounting.business_finance(target_firm,target_company)
   OR accounting.business_department_approver(target_firm,target_company,e.department_id)
   OR (e.status='handed_off' AND EXISTS(SELECT 1 FROM accounting.business_mappings m WHERE m.event_id=e.id AND accounting.book_allowed(m.book_id,true)))))
$$;
CREATE FUNCTION accounting.ai_event_reviewer(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_event_visible(target_event,target_firm,target_company) AND EXISTS(
  SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company
  AND e.applicant_id<>accounting.actor_user() AND (accounting.business_finance(target_firm,target_company)
   OR (e.status='handed_off' AND EXISTS(SELECT 1 FROM accounting.business_mappings m WHERE m.event_id=e.id AND accounting.ai_book_reviewer(m.book_id)))))
$$;
CREATE FUNCTION accounting.ai_bank_scope(target_event TEXT,target_firm TEXT,target_company TEXT,target_book TEXT,write_access BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.business_event_visible(target_event,target_firm,target_company)
 AND EXISTS(SELECT 1 FROM accounting.business_mappings m WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company AND m.book_id=target_book)
 AND ((NOT write_access AND (accounting.book_allowed(target_book) OR accounting.business_finance(target_firm,target_company)))
  OR (write_access AND (accounting.ai_book_reviewer(target_book) OR (accounting.business_finance(target_firm,target_company) AND accounting.book_allowed(target_book,true)))))
$$;

CREATE FUNCTION accounting.ai_run_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','stale') OR
   (NEW.id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.kind,NEW.attachment_id,NEW.draft_id,NEW.book_id,NEW.input_version,
    NEW.input_fingerprint,NEW.idempotency_key,NEW.provider,NEW.model,NEW.prompt_version,NEW.created_by,NEW.created_at)
   IS DISTINCT FROM
   (OLD.id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.kind,OLD.attachment_id,OLD.draft_id,OLD.book_id,OLD.input_version,
    OLD.input_fingerprint,OLD.idempotency_key,OLD.provider,OLD.model,OLD.prompt_version,OLD.created_by,OLD.created_at) THEN
   RAISE EXCEPTION 'AI run input and terminal output are immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER ai_run_identity BEFORE UPDATE ON accounting.ai_runs FOR EACH ROW EXECUTE FUNCTION accounting.ai_run_identity_guard();
CREATE FUNCTION accounting.ai_immutable_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN RAISE EXCEPTION 'AI evidence and audit history are immutable' USING ERRCODE='23514'; END
$$;
CREATE TRIGGER ai_runs_no_delete BEFORE DELETE ON accounting.ai_runs FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['ai_findings','ai_finding_reviews','ai_proposal_applications'] LOOP
  EXECUTE format('CREATE TRIGGER ai_record_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',item);
 END LOOP;
END $$;
CREATE TRIGGER ai_bank_links_no_update BEFORE UPDATE ON accounting.ai_bank_links FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.ai_bank_link_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE item RECORD;
 BEGIN
  item := CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
  IF NOT EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.drafts d ON d.id=m.draft_id AND d.book_id=m.book_id
   JOIN accounting.business_events e ON e.id=m.event_id WHERE m.event_id=item.event_id AND m.book_id=item.book_id AND d.status='draft') THEN
   RAISE EXCEPTION 'bank linkage requires an unapproved business draft' USING ERRCODE='23514';
  END IF;
  IF TG_OP='INSERT' AND NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id
   WHERE s.id=item.bank_source_id AND s.book_id=item.book_id AND s.source_kind='bank' AND d.status='draft') THEN
   RAISE EXCEPTION 'bank linkage requires an unapproved bank source' USING ERRCODE='23514';
  END IF;
  IF TG_OP='INSERT' AND NOT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=item.event_id AND e.payload::jsonb->>'payment_status'='paid') THEN
   RAISE EXCEPTION 'bank linkage requires a paid event' USING ERRCODE='23514';
  END IF;
  RETURN item;
 END
$$;
CREATE TRIGGER ai_bank_link_check BEFORE INSERT OR DELETE ON accounting.ai_bank_links FOR EACH ROW EXECUTE FUNCTION accounting.ai_bank_link_guard();

-- Limited cleanup after revocation: no row data is returned, and only the
-- original requester can abandon its own pending run. No output is published.
CREATE FUNCTION accounting.abandon_ai_run(target_run TEXT) RETURNS VOID
 LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
  error_code='authorization_changed',error_detail=''
 WHERE id=target_run AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm() AND status='running'
$$;
REVOKE ALL ON FUNCTION accounting.ai_company_visible(TEXT,TEXT),accounting.ai_book_reviewer(TEXT),
 accounting.ai_event_processor(TEXT,TEXT,TEXT),accounting.ai_event_reviewer(TEXT,TEXT,TEXT),accounting.ai_bank_scope(TEXT,TEXT,TEXT,TEXT,BOOLEAN),
 accounting.ai_run_identity_guard(),accounting.ai_immutable_guard(),accounting.ai_bank_link_guard(),accounting.abandon_ai_run(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.ai_company_visible(TEXT,TEXT),accounting.ai_book_reviewer(TEXT),accounting.ai_event_processor(TEXT,TEXT,TEXT),
 accounting.ai_event_reviewer(TEXT,TEXT,TEXT),accounting.ai_bank_scope(TEXT,TEXT,TEXT,TEXT,BOOLEAN),accounting.abandon_ai_run(TEXT) TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.ai_company_settings,accounting.ai_runs TO accounting_app;
GRANT SELECT,INSERT ON accounting.ai_findings,accounting.ai_finding_reviews,accounting.ai_proposal_applications TO accounting_app;
GRANT SELECT,INSERT,DELETE ON accounting.ai_bank_links TO accounting_app;
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['ai_company_settings','ai_runs','ai_findings','ai_finding_reviews','ai_proposal_applications','ai_bank_links'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 END LOOP;
END $$;
CREATE POLICY ai_settings_read ON accounting.ai_company_settings FOR SELECT TO accounting_app
 USING(accounting.ai_company_visible(firm_id,company_id));
CREATE POLICY ai_settings_create ON accounting.ai_company_settings FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id) AND updated_by=accounting.actor_user());
CREATE POLICY ai_settings_update ON accounting.ai_company_settings FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id) AND updated_by=accounting.actor_user());
CREATE POLICY ai_runs_read ON accounting.ai_runs FOR SELECT TO accounting_app
 USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY ai_runs_create ON accounting.ai_runs FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND status='running' AND accounting.ai_event_processor(event_id,firm_id,company_id)
  AND (kind='ocr' OR accounting.book_allowed(book_id,true)));
CREATE POLICY ai_runs_finish ON accounting.ai_runs FOR UPDATE TO accounting_app
 USING(created_by=accounting.actor_user() AND status='running' AND accounting.ai_event_processor(event_id,firm_id,company_id))
 WITH CHECK(created_by=accounting.actor_user() AND accounting.ai_event_processor(event_id,firm_id,company_id));
CREATE POLICY ai_findings_read ON accounting.ai_findings FOR SELECT TO accounting_app
 USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY ai_findings_create ON accounting.ai_findings FOR INSERT TO accounting_app
 WITH CHECK(accounting.ai_event_processor(event_id,firm_id,company_id));
CREATE POLICY ai_reviews_read ON accounting.ai_finding_reviews FOR SELECT TO accounting_app
 USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY ai_reviews_create ON accounting.ai_finding_reviews FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.ai_event_reviewer(event_id,firm_id,company_id));
CREATE POLICY ai_applications_read ON accounting.ai_proposal_applications FOR SELECT TO accounting_app
 USING(accounting.business_event_visible(event_id,firm_id,company_id) AND (accounting.book_allowed(book_id) OR accounting.business_finance(firm_id,company_id)));
CREATE POLICY ai_applications_create ON accounting.ai_proposal_applications FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.business_event_visible(event_id,firm_id,company_id) AND accounting.book_allowed(book_id,true));
CREATE POLICY ai_links_read ON accounting.ai_bank_links FOR SELECT TO accounting_app
 USING(accounting.ai_bank_scope(event_id,firm_id,company_id,book_id));
CREATE POLICY ai_links_create ON accounting.ai_bank_links FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.ai_bank_scope(event_id,firm_id,company_id,book_id,true));
CREATE POLICY ai_links_delete ON accounting.ai_bank_links FOR DELETE TO accounting_app
 USING(accounting.ai_bank_scope(event_id,firm_id,company_id,book_id,true));
