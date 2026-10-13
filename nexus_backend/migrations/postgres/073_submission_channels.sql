-- Existing events retain their original approval channel; a policy only affects new events.
CREATE TABLE accounting.business_submission_policies(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN('department','direct')),
 version INTEGER NOT NULL CHECK(version>0),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 updated_by TEXT NOT NULL REFERENCES accounting.users(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
ALTER TABLE accounting.business_submission_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.business_submission_policies FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON accounting.business_submission_policies TO accounting_app;
CREATE POLICY submission_policy_read ON accounting.business_submission_policies FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.business_member(firm_id,company_id) OR accounting.company_manager(company_id) OR EXISTS(SELECT 1 FROM accounting.books b WHERE b.company_id=business_submission_policies.company_id AND b.firm_id=business_submission_policies.firm_id AND accounting.book_allowed(b.id))));
CREATE POLICY submission_policy_create ON accounting.business_submission_policies FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id) AND updated_by=accounting.actor_user());
CREATE POLICY submission_policy_edit ON accounting.business_submission_policies FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id) AND updated_by=accounting.actor_user());
CREATE FUNCTION accounting.submission_policy_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE previous_version INTEGER; BEGIN
 IF TG_OP='INSERT' THEN
  -- INSERT ... ON CONFLICT first runs the insert trigger for proposed updates.
  SELECT version INTO previous_version FROM accounting.business_submission_policies WHERE firm_id=NEW.firm_id AND company_id=NEW.company_id;
  IF NEW.version<>coalesce(previous_version,0)+1 THEN
   RAISE EXCEPTION 'Submission policy starts at one and advances one version' USING ERRCODE='23514'; END IF;
 ELSIF (NEW.firm_id,NEW.company_id) IS DISTINCT FROM (OLD.firm_id,OLD.company_id) OR NEW.version<>OLD.version+1 THEN
  RAISE EXCEPTION 'Submission policy scope and consecutive version are protected' USING ERRCODE='23514';
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER submission_policy_scope BEFORE INSERT OR UPDATE ON accounting.business_submission_policies FOR EACH ROW EXECUTE FUNCTION accounting.submission_policy_guard();
REVOKE ALL ON FUNCTION accounting.submission_policy_guard() FROM PUBLIC;
ALTER TABLE accounting.business_events ADD COLUMN submission_mode TEXT NOT NULL DEFAULT 'department' CHECK(submission_mode IN('department','direct'));
CREATE FUNCTION accounting.direct_business_receiver(target_firm TEXT,target_company TEXT,target_applicant TEXT,target_payload TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND target_applicant<>accounting.actor_user()
 AND EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id
  WHERE fc.status='active' AND b.firm_id=target_firm AND b.company_id=target_company
  AND b.period=left(target_payload::jsonb->>'occurred_on',7) AND accounting.book_allowed(b.id,true))
$$;
CREATE FUNCTION accounting.direct_business_event(target_event TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.submission_mode='direct'
  AND accounting.direct_business_receiver(e.firm_id,e.company_id,e.applicant_id,e.payload))
$$;
CREATE OR REPLACE FUNCTION accounting.business_row_visible(target_event TEXT,target_firm TEXT,target_company TEXT,target_department TEXT,target_applicant TEXT,target_status TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active') AND (
  (accounting.business_member(target_firm,target_company) AND target_applicant=accounting.actor_user())
  OR accounting.business_finance(target_firm,target_company)
  OR accounting.business_department_approver(target_firm,target_company,target_department)
  OR (target_status IN('submitted','returned') AND accounting.direct_business_event(target_event))
  OR (target_status='handed_off' AND EXISTS(SELECT 1 FROM accounting.business_mappings m
   WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company AND accounting.book_allowed(m.book_id)))
 )
$$;
DROP POLICY business_events_update ON accounting.business_events;
CREATE POLICY business_events_update ON accounting.business_events FOR UPDATE TO accounting_app
 USING(accounting.business_own_editable(id,firm_id,company_id) OR accounting.business_approver_write(id,firm_id,company_id,true) OR accounting.direct_business_event(id))
 WITH CHECK(accounting.business_event_visible(id,firm_id,company_id));
DROP POLICY business_mappings_create ON accounting.business_mappings;
CREATE POLICY business_mappings_create ON accounting.business_mappings FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.book_allowed(book_id,true)
 AND accounting.business_event_visible(event_id,firm_id,company_id)
 AND EXISTS(SELECT 1 FROM accounting.business_events e
  WHERE e.id=event_id AND e.firm_id=business_mappings.firm_id AND e.company_id=business_mappings.company_id
  AND ((e.submission_mode='department' AND accounting.business_finance(firm_id,company_id))
   OR (e.submission_mode='direct' AND e.status='submitted' AND business_mappings.submitted_version=e.submitted_version AND accounting.direct_business_event(e.id)
    AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=business_mappings.book_id AND b.firm_id=e.firm_id AND b.company_id=e.company_id AND b.period=left(e.payload::jsonb->>'occurred_on',7))))));
DROP POLICY business_actions_create ON accounting.business_actions;
CREATE POLICY business_actions_create ON accounting.business_actions FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND accounting.business_event_visible(event_id,firm_id,company_id)
 AND EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=event_id AND e.firm_id=business_actions.firm_id AND e.company_id=business_actions.company_id
  AND ((e.submission_mode='department' AND accounting.business_approver_write(event_id,firm_id,company_id,stage='handoff' AND decision='handoff'))
   OR (e.submission_mode='direct' AND business_actions.submitted_version=e.submitted_version AND accounting.direct_business_event(event_id)
    AND ((stage='return' AND decision='return' AND e.status='returned')
     OR (stage='handoff' AND decision='handoff' AND e.status='handed_off' AND EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.books b ON b.id=m.book_id
       WHERE m.event_id=e.id AND m.submitted_version=e.submitted_version AND m.created_by=accounting.actor_user() AND accounting.book_allowed(m.book_id,true)
        AND b.firm_id=e.firm_id AND b.company_id=e.company_id AND b.period=left(e.payload::jsonb->>'occurred_on',7))))))));
CREATE FUNCTION accounting.submission_channel_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE channel TEXT; BEGIN
 IF TG_OP='INSERT' THEN
  SELECT COALESCE((SELECT mode FROM accounting.business_submission_policies WHERE firm_id=NEW.firm_id AND company_id=NEW.company_id),'department') INTO channel;
  IF NEW.submission_mode<>channel OR (channel='direct' AND NOT EXISTS(SELECT 1 FROM accounting.company_memberships cm
   WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.applicant_id AND cm.role IN('owner','admin','finance'))) THEN
   RAISE EXCEPTION 'Invalid company submission channel' USING ERRCODE='23514'; END IF;
 ELSIF NEW.submission_mode IS DISTINCT FROM OLD.submission_mode THEN
  RAISE EXCEPTION 'Submission channel is frozen per event' USING ERRCODE='23514';
 ELSIF OLD.submission_mode='direct' AND OLD.status<>'handed_off'
  AND NEW.submitted_version IS DISTINCT FROM OLD.submitted_version
  AND NOT (OLD.status IN('draft','returned') AND NEW.status='submitted' AND OLD.applicant_id=accounting.actor_user()) THEN
  RAISE EXCEPTION 'Only applicant resubmission changes the frozen snapshot pointer' USING ERRCODE='23514';
 ELSIF OLD.submission_mode='direct' AND NEW.payload IS DISTINCT FROM OLD.payload
  AND OLD.status<>'handed_off' AND NOT (OLD.status IN('draft','returned') AND OLD.applicant_id=accounting.actor_user()) THEN
  RAISE EXCEPTION 'Only the applicant edits unsubmitted direct business facts' USING ERRCODE='23514';
 ELSIF OLD.submission_mode='direct' AND NEW.status IS DISTINCT FROM OLD.status THEN
  IF OLD.status IN('draft','returned') AND NEW.status='submitted' THEN
   IF NEW.applicant_id<>accounting.actor_user() OR NOT EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.applicant_id AND cm.role IN('owner','admin','finance')) OR NEW.submitted_version<>NEW.version
    OR NOT EXISTS(SELECT 1 FROM accounting.business_versions v WHERE v.event_id=NEW.id AND v.event_version=NEW.submitted_version AND v.payload=NEW.payload)
    OR NOT EXISTS(SELECT 1 FROM accounting.business_attachments a WHERE a.event_id=NEW.id) THEN
    RAISE EXCEPTION 'Direct submission requires frozen applicant evidence' USING ERRCODE='23514'; END IF;
  ELSIF OLD.status='submitted' AND NEW.status IN('returned','handed_off') THEN
   IF NOT accounting.direct_business_receiver(OLD.firm_id,OLD.company_id,OLD.applicant_id,OLD.payload)
    OR (NEW.status='handed_off' AND NOT EXISTS(SELECT 1 FROM accounting.business_mappings m WHERE m.event_id=NEW.id AND m.submitted_version=NEW.submitted_version AND m.created_by=accounting.actor_user())) THEN
    RAISE EXCEPTION 'Direct evidence requires an independent authorized receiver' USING ERRCODE='23514'; END IF;
  ELSE RAISE EXCEPTION 'Invalid direct submission transition' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER submission_channel_scope BEFORE INSERT OR UPDATE ON accounting.business_events FOR EACH ROW EXECUTE FUNCTION accounting.submission_channel_guard();
ALTER TABLE accounting.intake_drafts ALTER COLUMN department_id DROP NOT NULL;
-- SQL equality would reject a correctly matched null department at completion.
CREATE OR REPLACE FUNCTION accounting.intake_draft_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' OR NEW.version<>1 THEN RAISE EXCEPTION 'Intake starts incomplete at version one'; END IF;
 ELSE
  IF OLD.status<>'draft' OR (NEW.id,NEW.firm_id,NEW.company_id,NEW.department_id,NEW.created_by,NEW.create_key,NEW.create_fingerprint,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.department_id,OLD.created_by,OLD.create_key,OLD.create_fingerprint,OLD.created_at) OR NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'Intake identity and completed evidence are immutable'; END IF;
  IF NEW.status='completed' AND (NOT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=NEW.event_id AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.department_id IS NOT DISTINCT FROM NEW.department_id AND e.applicant_id=NEW.created_by AND e.template_id=NEW.template_id AND e.payload::jsonb=NEW.fields_json::jsonb AND e.status IN('draft','submitted')) OR EXISTS(SELECT 1 FROM accounting.intake_attachments a WHERE a.intake_id=NEW.id AND NOT EXISTS(SELECT 1 FROM accounting.business_attachments b WHERE b.id=a.id AND b.event_id=NEW.event_id AND b.storage_key=a.storage_key AND b.sha256=a.sha256 AND b.size_bytes=a.size_bytes))) THEN RAISE EXCEPTION 'Completion must reference matching confirmed facts and originals'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE FUNCTION accounting.intake_channel_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.department_id IS NULL AND NOT EXISTS(SELECT 1 FROM accounting.business_submission_policies p JOIN accounting.company_memberships cm ON cm.company_id=p.company_id
  WHERE p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.mode='direct' AND cm.user_id=NEW.created_by AND cm.role IN('owner','admin','finance')) THEN
  RAISE EXCEPTION 'A department is required for the configured intake channel' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER intake_channel_scope BEFORE INSERT ON accounting.intake_drafts FOR EACH ROW EXECUTE FUNCTION accounting.intake_channel_guard();
REVOKE ALL ON FUNCTION accounting.direct_business_receiver(TEXT,TEXT,TEXT,TEXT),accounting.direct_business_event(TEXT),accounting.submission_channel_guard(),accounting.intake_channel_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.direct_business_receiver(TEXT,TEXT,TEXT,TEXT),accounting.direct_business_event(TEXT) TO accounting_app;
