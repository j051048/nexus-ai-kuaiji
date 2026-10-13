SET LOCAL search_path TO accounting,pg_catalog;
CREATE TABLE case_tasks(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT,book_id TEXT,source_id TEXT,draft_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('exception','supplement')),title TEXT NOT NULL,request_reason TEXT NOT NULL,
 required_fields TEXT NOT NULL DEFAULT '[]',attachment_required INTEGER NOT NULL DEFAULT 0 CHECK(attachment_required IN (0,1)),
 recipient_id TEXT,assignee_id TEXT,priority TEXT NOT NULL CHECK(priority IN ('normal','high','urgent')),due_on TEXT,
 state TEXT NOT NULL CHECK(state IN ('open','awaiting_info','response_submitted','in_review','closed')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),evidence_revision INTEGER NOT NULL DEFAULT 0 CHECK(evidence_revision>=0),
 accepted_fact_revision INTEGER NOT NULL DEFAULT 0 CHECK(accepted_fact_revision>=0),created_by TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,firm_id,company_id),CHECK(event_id IS NOT NULL OR (book_id IS NOT NULL AND source_id IS NOT NULL AND draft_id IS NOT NULL)),
 CHECK(kind<>'supplement' OR event_id IS NOT NULL),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),FOREIGN KEY(draft_id,source_id,book_id) REFERENCES drafts(id,source_id,book_id),
 FOREIGN KEY(recipient_id) REFERENCES users(id),FOREIGN KEY(assignee_id) REFERENCES users(id),FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TABLE case_responses(
 id TEXT PRIMARY KEY,case_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('draft','submitted','accepted','returned')),message TEXT NOT NULL DEFAULT '',fields TEXT NOT NULL DEFAULT '{}',
 created_by TEXT NOT NULL,created_at TEXT NOT NULL,submitted_at TEXT,reviewed_by TEXT,reviewed_at TEXT,review_reason TEXT NOT NULL DEFAULT '',supersedes_attachment_ids TEXT NOT NULL DEFAULT '[]',
 UNIQUE(id,case_id),FOREIGN KEY(case_id,firm_id,company_id) REFERENCES case_tasks(id,firm_id,company_id),
 FOREIGN KEY(created_by) REFERENCES users(id),FOREIGN KEY(reviewed_by) REFERENCES users(id)
);
CREATE UNIQUE INDEX idx_case_response_current ON case_responses(case_id) WHERE state IN ('draft','submitted');
CREATE TABLE case_attachments(
 id TEXT PRIMARY KEY,response_id TEXT NOT NULL,case_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,media_type TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=10485760),
 created_by TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(response_id,case_id) REFERENCES case_responses(id,case_id),
 FOREIGN KEY(case_id,firm_id,company_id) REFERENCES case_tasks(id,firm_id,company_id),FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TABLE case_history(
 id TEXT PRIMARY KEY,case_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,case_version INTEGER NOT NULL,
 action TEXT NOT NULL,audience TEXT NOT NULL CHECK(audience IN ('internal','requester')),reason TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(case_id,firm_id,company_id) REFERENCES case_tasks(id,firm_id,company_id),FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TABLE case_commands(
 firm_id TEXT NOT NULL,user_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,case_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
 result_version INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(firm_id,user_id,idempotency_key),
 FOREIGN KEY(case_id) REFERENCES case_tasks(id),FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX idx_cases_page ON case_tasks(firm_id,state,priority,due_on,updated_at,id);
CREATE INDEX idx_cases_event ON case_tasks(event_id,state);
CREATE INDEX idx_cases_draft ON case_tasks(draft_id,state);
CREATE INDEX idx_cases_company ON case_tasks(firm_id,company_id,state,updated_at);
CREATE INDEX idx_case_history_scope ON case_history(case_id,audience,created_at,id);
CREATE TABLE accounting.case_supersessions(
 old_attachment_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,case_id TEXT NOT NULL,response_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 accepted_by TEXT NOT NULL,accepted_at TEXT NOT NULL,FOREIGN KEY(old_attachment_id,event_id) REFERENCES accounting.business_attachments(id,event_id),
 FOREIGN KEY(response_id,case_id) REFERENCES accounting.case_responses(id,case_id),FOREIGN KEY(case_id,firm_id,company_id) REFERENCES accounting.case_tasks(id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),FOREIGN KEY(accepted_by) REFERENCES accounting.users(id)
);

CREATE FUNCTION accounting.case_scope_manage(target_firm TEXT,target_company TEXT,target_event TEXT,target_book TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 WITH current_target AS(SELECT COALESCE(target_book,(SELECT m.book_id FROM accounting.business_mappings m WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company)) AS book_id)
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active') AND (
   (target_event IS NOT NULL AND accounting.business_finance(target_firm,target_company) AND EXISTS(
    SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company AND e.applicant_id<>accounting.actor_user()))
   OR (current_target.book_id IS NOT NULL AND accounting.ai_book_reviewer(current_target.book_id) AND EXISTS(
    SELECT 1 FROM accounting.books b WHERE b.id=current_target.book_id AND b.firm_id=target_firm AND b.company_id=target_company)
    AND (target_event IS NULL OR EXISTS(SELECT 1 FROM accounting.business_events e JOIN accounting.business_mappings m ON m.event_id=e.id
      WHERE e.id=target_event AND e.status='handed_off' AND m.book_id=current_target.book_id)))) FROM current_target
$$;
CREATE FUNCTION accounting.case_manage(target_case TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.case_tasks t WHERE t.id=target_case AND accounting.case_scope_manage(t.firm_id,t.company_id,t.event_id,t.book_id))
$$;
CREATE FUNCTION accounting.case_recipient(target_case TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.case_tasks t WHERE t.id=target_case AND t.kind='supplement' AND t.recipient_id=accounting.actor_user()
  AND accounting.business_member(t.firm_id,t.company_id))
$$;
CREATE FUNCTION accounting.case_visible(target_case TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.case_manage(target_case) OR accounting.case_recipient(target_case) OR EXISTS(
  SELECT 1 FROM accounting.case_tasks t WHERE t.id=target_case AND t.kind='exception' AND t.event_id IS NULL
   AND t.assignee_id=accounting.actor_user() AND accounting.business_finance(t.firm_id,t.company_id) AND accounting.book_allowed(t.book_id,true))
$$;
CREATE FUNCTION accounting.case_collector(target_case TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.case_tasks t WHERE t.id=target_case AND t.kind='exception' AND t.event_id IS NULL
  AND t.assignee_id=accounting.actor_user() AND accounting.business_finance(t.firm_id,t.company_id) AND accounting.book_allowed(t.book_id,true))
$$;
CREATE FUNCTION accounting.case_collection_open(target_case TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.case_collector(target_case) AND EXISTS(SELECT 1 FROM accounting.case_tasks t WHERE t.id=target_case AND t.state='open')
$$;
CREATE FUNCTION accounting.case_response_editable(target_response TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.case_responses r JOIN accounting.case_tasks t ON t.id=r.case_id
  WHERE r.id=target_response AND r.state='draft' AND r.created_by=accounting.actor_user() AND
   ((t.state='awaiting_info' AND accounting.case_recipient(t.id)) OR (t.kind='exception' AND t.event_id IS NULL AND t.state='open' AND (accounting.case_manage(t.id) OR accounting.case_collector(t.id)))))
$$;
CREATE FUNCTION accounting.case_source_open(target_case TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.case_visible(target_case) AND EXISTS(SELECT 1 FROM accounting.case_tasks t LEFT JOIN accounting.business_mappings m ON m.event_id=t.event_id WHERE t.id=target_case AND
  (COALESCE(t.book_id,m.book_id) IS NULL OR EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
   JOIN accounting.drafts d ON d.id=COALESCE(t.draft_id,m.draft_id) AND d.book_id=b.id AND d.source_id=COALESCE(t.source_id,m.source_id) WHERE b.id=COALESCE(t.book_id,m.book_id) AND p.status='open' AND d.status='draft')))
$$;
CREATE FUNCTION accounting.case_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.book_id,NEW.source_id,NEW.draft_id,NEW.kind,NEW.recipient_id,NEW.created_by,NEW.created_at,NEW.title,NEW.request_reason,NEW.required_fields,NEW.attachment_required)
   IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.book_id,OLD.source_id,OLD.draft_id,OLD.kind,OLD.recipient_id,OLD.created_by,OLD.created_at,OLD.title,OLD.request_reason,OLD.required_fields,OLD.attachment_required) THEN
   RAISE EXCEPTION 'Case identity and original request are immutable' USING ERRCODE='23514';
  END IF;
  IF NOT accounting.case_manage(OLD.id) AND ((NEW.assignee_id,NEW.priority,NEW.due_on,NEW.evidence_revision,NEW.accepted_fact_revision)
   IS DISTINCT FROM (OLD.assignee_id,OLD.priority,OLD.due_on,OLD.evidence_revision,OLD.accepted_fact_revision)
   OR (NOT ((OLD.state='awaiting_info' AND accounting.case_recipient(OLD.id) AND NEW.state IN ('awaiting_info','response_submitted'))
    OR (OLD.state='open' AND accounting.case_collector(OLD.id) AND NEW.state IN ('open','response_submitted')))) OR NEW.version<>OLD.version+1) THEN
   RAISE EXCEPTION 'Applicant may only upload/respond to current supplement request' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER case_identity BEFORE UPDATE ON accounting.case_tasks FOR EACH ROW EXECUTE FUNCTION accounting.case_identity_guard();
CREATE FUNCTION accounting.case_response_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.id,NEW.case_id,NEW.firm_id,NEW.company_id,NEW.created_by,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.case_id,OLD.firm_id,OLD.company_id,OLD.created_by,OLD.created_at)
   OR OLD.state IN ('accepted','returned') OR (OLD.state='submitted' AND
    (NEW.message,NEW.fields,NEW.submitted_at) IS DISTINCT FROM (OLD.message,OLD.fields,OLD.submitted_at)) THEN
   RAISE EXCEPTION 'Submitted supplement facts are immutable' USING ERRCODE='23514';
  END IF;
  IF NOT accounting.case_manage(OLD.case_id) AND (OLD.state<>'draft' OR NEW.state NOT IN ('draft','submitted')
   OR (NEW.reviewed_by,NEW.reviewed_at,NEW.review_reason,NEW.supersedes_attachment_ids) IS DISTINCT FROM (OLD.reviewed_by,OLD.reviewed_at,OLD.review_reason,OLD.supersedes_attachment_ids)) THEN
   RAISE EXCEPTION 'Applicant may submit facts but cannot accept their own evidence' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER case_response_identity BEFORE UPDATE ON accounting.case_responses FOR EACH ROW EXECUTE FUNCTION accounting.case_response_guard();
CREATE FUNCTION accounting.case_accept_evidence(target_case TEXT,target_version INTEGER) RETURNS VOID
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE task accounting.case_tasks%ROWTYPE; reply accounting.case_responses%ROWTYPE; old_id TEXT; current_book TEXT; current_draft TEXT; current_source TEXT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO task FROM accounting.case_tasks WHERE id=target_case FOR UPDATE;
  IF NOT FOUND OR NOT accounting.case_manage(target_case) OR task.version<>target_version OR task.state<>'in_review'
    OR task.evidence_revision<>task.accepted_fact_revision+1 THEN
   RAISE EXCEPTION 'Current supplement acceptance authorization/version required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO reply FROM accounting.case_responses WHERE case_id=task.id AND state='accepted'
   AND reviewed_by=accounting.actor_user() ORDER BY reviewed_at DESC,id DESC LIMIT 1;
  IF NOT FOUND OR reply.created_by=accounting.actor_user() OR NOT EXISTS(SELECT 1 FROM accounting.case_commands c
   WHERE c.case_id=task.id AND c.firm_id=task.firm_id AND c.user_id=accounting.actor_user() AND c.result_version=target_version) THEN
   RAISE EXCEPTION 'Accepted response and audited command required' USING ERRCODE='42501';
  END IF;
  SELECT COALESCE(task.book_id,m.book_id),COALESCE(task.draft_id,m.draft_id),COALESCE(task.source_id,m.source_id)
   INTO current_book,current_draft,current_source FROM (VALUES(1)) AS singleton(id) LEFT JOIN accounting.business_mappings m ON m.event_id=task.event_id;
  IF current_book IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
   JOIN accounting.drafts d ON d.id=current_draft AND d.book_id=b.id AND d.source_id=current_source
   WHERE b.id=current_book AND b.firm_id=task.firm_id AND b.company_id=task.company_id AND p.status='open' AND d.status='draft') THEN
   RAISE EXCEPTION 'Open period and unreviewed draft required' USING ERRCODE='42501';
  END IF;
  IF task.event_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM accounting.business_events e JOIN accounting.company_memberships cm ON cm.company_id=e.company_id AND cm.user_id=e.applicant_id
    JOIN accounting.users u ON u.id=cm.user_id AND u.active=1 WHERE e.id=task.event_id AND e.applicant_id=reply.created_by AND e.firm_id=task.firm_id AND e.company_id=task.company_id) THEN
    RAISE EXCEPTION 'Current applicant authorization required' USING ERRCODE='42501';
   END IF;
   FOR old_id IN SELECT jsonb_array_elements_text(reply.supersedes_attachment_ids::jsonb) LOOP
    IF NOT EXISTS(SELECT 1 FROM accounting.business_attachments a WHERE a.id=old_id AND a.event_id=task.event_id AND a.firm_id=task.firm_id AND a.company_id=task.company_id)
      OR EXISTS(SELECT 1 FROM accounting.case_supersessions s WHERE s.old_attachment_id=old_id)
      OR NOT EXISTS(SELECT 1 FROM accounting.case_attachments a WHERE a.response_id=reply.id AND a.case_id=task.id)
      OR EXISTS(SELECT 1 FROM accounting.case_attachments a WHERE a.id=old_id AND a.response_id=reply.id) THEN
     RAISE EXCEPTION 'Supersession requires current same-event originals and replacement attachments' USING ERRCODE='42501';
    END IF;
    INSERT INTO accounting.case_supersessions(old_attachment_id,event_id,case_id,response_id,firm_id,company_id,accepted_by,accepted_at)
     VALUES(old_id,task.event_id,task.id,reply.id,task.firm_id,task.company_id,accounting.actor_user(),task.updated_at);
   END LOOP;
   INSERT INTO accounting.business_attachments(id,event_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_by,created_at)
    SELECT a.id,task.event_id,a.firm_id,a.company_id,a.storage_key,a.filename,a.media_type,a.sha256,a.size_bytes,a.created_by,a.created_at
    FROM accounting.case_attachments a WHERE a.response_id=reply.id AND a.case_id=task.id AND a.firm_id=task.firm_id AND a.company_id=task.company_id;
   UPDATE accounting.business_events SET fact_version=fact_version+1,version=version+1,updated_at=task.updated_at
    WHERE id=task.event_id AND firm_id=task.firm_id AND company_id=task.company_id;
  ELSE
   INSERT INTO accounting.source_evidence(source_id,book_id,evidence_ref,created_at,created_by)
    VALUES(task.source_id,task.book_id,'已核验补件 case:'||task.id||'；'||reply.message,task.updated_at,accounting.actor_user());
   UPDATE accounting.drafts SET version=version+1 WHERE id=task.draft_id AND book_id=task.book_id AND status='draft';
  END IF;
  UPDATE accounting.case_tasks SET accepted_fact_revision=evidence_revision WHERE id=task.id;
 END
$$;

DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['case_tasks','case_responses','case_attachments','case_history','case_commands','case_supersessions'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 END LOOP;
END $$;
GRANT SELECT,INSERT,UPDATE ON accounting.case_tasks,accounting.case_responses TO accounting_app;
GRANT SELECT,INSERT ON accounting.case_attachments,accounting.case_history,accounting.case_commands TO accounting_app;
CREATE POLICY case_read ON accounting.case_tasks FOR SELECT TO accounting_app USING(accounting.case_manage(id) OR accounting.case_recipient(id));
CREATE POLICY case_create ON accounting.case_tasks FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.case_scope_manage(firm_id,company_id,event_id,book_id)
 AND version=1 AND evidence_revision=0 AND accepted_fact_revision=0 AND ((kind='exception' AND recipient_id IS NULL AND state='open') OR
 (kind='supplement' AND state='awaiting_info' AND EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=event_id AND e.applicant_id=recipient_id))));
CREATE POLICY case_update ON accounting.case_tasks FOR UPDATE TO accounting_app USING(accounting.case_manage(id) OR accounting.case_recipient(id)) WITH CHECK(accounting.case_visible(id));
CREATE POLICY case_response_read ON accounting.case_responses FOR SELECT TO accounting_app USING(accounting.case_manage(case_id) OR (accounting.case_visible(case_id) AND created_by=accounting.actor_user()));
CREATE POLICY case_response_create ON accounting.case_responses FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND state='draft' AND
 (accounting.case_recipient(case_id) OR accounting.case_collection_open(case_id) OR (accounting.case_manage(case_id) AND EXISTS(SELECT 1 FROM accounting.case_tasks t WHERE t.id=case_id AND t.kind='exception' AND t.event_id IS NULL AND t.state='open'))));
CREATE POLICY case_response_update ON accounting.case_responses FOR UPDATE TO accounting_app USING(accounting.case_response_editable(id) OR accounting.case_manage(case_id)) WITH CHECK(accounting.case_visible(case_id));
CREATE POLICY case_attachment_read ON accounting.case_attachments FOR SELECT TO accounting_app USING(accounting.case_manage(case_id) OR (accounting.case_visible(case_id) AND created_by=accounting.actor_user()));
CREATE POLICY case_attachment_create ON accounting.case_attachments FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.case_response_editable(response_id));
CREATE POLICY case_history_read ON accounting.case_history FOR SELECT TO accounting_app USING(accounting.case_manage(case_id) OR (audience='requester' AND (accounting.case_recipient(case_id) OR accounting.case_collector(case_id))));
CREATE POLICY case_history_create ON accounting.case_history FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND (accounting.case_manage(case_id) OR (audience='requester' AND (accounting.case_recipient(case_id) OR accounting.case_collector(case_id)))));
CREATE POLICY case_command_read ON accounting.case_commands FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND user_id=accounting.actor_user() AND accounting.case_visible(case_id));
CREATE POLICY case_command_create ON accounting.case_commands FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND user_id=accounting.actor_user() AND accounting.case_visible(case_id));
GRANT SELECT ON accounting.case_supersessions TO accounting_app;
CREATE POLICY case_supersession_read ON accounting.case_supersessions FOR SELECT TO accounting_app USING(accounting.business_event_visible(event_id,firm_id,company_id));
REVOKE ALL ON FUNCTION accounting.case_scope_manage(TEXT,TEXT,TEXT,TEXT),accounting.case_manage(TEXT),accounting.case_recipient(TEXT),accounting.case_visible(TEXT),
 accounting.case_response_editable(TEXT),accounting.case_identity_guard(),accounting.case_response_guard(),accounting.case_accept_evidence(TEXT,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.case_scope_manage(TEXT,TEXT,TEXT,TEXT),accounting.case_manage(TEXT),accounting.case_recipient(TEXT),accounting.case_visible(TEXT),
 accounting.case_response_editable(TEXT),accounting.case_accept_evidence(TEXT,INTEGER) TO accounting_app;

CREATE FUNCTION accounting.case_assignee_allowed(target_case TEXT,target_user TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 WITH target AS(
  SELECT t.*,COALESCE(t.book_id,bm.book_id) AS current_book FROM accounting.case_tasks t
   LEFT JOIN accounting.business_mappings bm ON bm.event_id=t.event_id WHERE t.id=target_case
 ), candidate AS(
  SELECT t.event_id,t.kind,t.current_book,u.id AS user_id,
   (SELECT m.role FROM accounting.memberships m WHERE m.firm_id=t.firm_id AND m.user_id=u.id) AS firm_role,
   (SELECT cm.role FROM accounting.company_memberships cm WHERE cm.company_id=t.company_id AND cm.user_id=u.id) AS company_role,
   (SELECT a.role FROM accounting.book_access a WHERE a.book_id=t.current_book AND a.firm_id=t.firm_id AND a.user_id=u.id) AS book_role,
   (SELECT e.applicant_id FROM accounting.business_events e WHERE e.id=t.event_id) AS applicant_id
  FROM target t JOIN accounting.users u ON u.id=target_user AND u.active=1
 )
 SELECT accounting.case_manage(target_case) AND EXISTS(SELECT 1 FROM candidate c WHERE
  (c.event_id IS NOT NULL AND c.company_role='finance' AND c.applicant_id<>c.user_id)
  OR (c.current_book IS NOT NULL AND (c.firm_role IN ('owner','admin') OR
   (c.book_role='reviewer' AND (c.firm_role='reviewer' OR (c.firm_role IS NULL AND c.company_role='finance')))))
  OR (c.event_id IS NULL AND c.kind='exception' AND c.company_role='finance' AND c.book_role IN ('editor','reviewer')
   AND (c.firm_role IS NULL OR c.firm_role IN ('owner','admin','operator','reviewer')))
 )
$$;
CREATE FUNCTION accounting.case_assignees(target_case TEXT) RETURNS TABLE(user_id TEXT,name TEXT)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT u.id,u.name FROM accounting.users u WHERE accounting.case_manage(target_case) AND accounting.case_assignee_allowed(target_case,u.id) ORDER BY u.name,u.id
$$;
REVOKE ALL ON FUNCTION accounting.case_assignee_allowed(TEXT,TEXT),accounting.case_assignees(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.case_assignee_allowed(TEXT,TEXT),accounting.case_assignees(TEXT) TO accounting_app;
REVOKE ALL ON FUNCTION accounting.case_source_open(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.case_source_open(TEXT) TO accounting_app;
REVOKE ALL ON FUNCTION accounting.case_collector(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.case_collector(TEXT) TO accounting_app;
REVOKE ALL ON FUNCTION accounting.case_collection_open(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.case_collection_open(TEXT) TO accounting_app;

CREATE FUNCTION accounting.case_collector_tasks() RETURNS SETOF accounting.case_tasks
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE task accounting.case_tasks%ROWTYPE;
 BEGIN
  FOR task IN SELECT * FROM accounting.case_tasks t WHERE t.firm_id=accounting.actor_firm() AND t.assignee_id=accounting.actor_user()
   AND accounting.case_collector(t.id) AND NOT accounting.case_manage(t.id) LOOP
   task.title:='银行流水资料补充';
   task.request_reason:=COALESCE((SELECT h.reason FROM accounting.case_history h WHERE h.case_id=task.id AND h.audience='requester' ORDER BY h.created_at DESC,h.id DESC LIMIT 1),'请补充已指派的业务资料');
   RETURN NEXT task;
  END LOOP;
 END
$$;
CREATE FUNCTION accounting.case_collection_update(target_case TEXT,target_version INTEGER,target_state TEXT,target_time TEXT) RETURNS VOID
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE task accounting.case_tasks%ROWTYPE;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO task FROM accounting.case_tasks WHERE id=target_case FOR UPDATE;
  IF NOT FOUND OR NOT accounting.case_collector(target_case) OR task.version<>target_version OR task.state<>'open'
   OR target_state NOT IN ('open','response_submitted') OR NOT accounting.case_source_open(target_case) THEN
   RAISE EXCEPTION 'Current assigned finance collection/version required' USING ERRCODE='42501';
  END IF;
  IF target_state='response_submitted' AND NOT EXISTS(SELECT 1 FROM accounting.case_responses r WHERE r.case_id=task.id AND r.created_by=accounting.actor_user() AND r.state='submitted') THEN
   RAISE EXCEPTION 'Submitted collection response required' USING ERRCODE='42501';
  END IF;
  UPDATE accounting.case_tasks SET state=target_state,version=version+1,updated_at=target_time WHERE id=task.id;
 END
$$;
REVOKE ALL ON FUNCTION accounting.case_collector_tasks(),accounting.case_collection_update(TEXT,INTEGER,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.case_collector_tasks(),accounting.case_collection_update(TEXT,INTEGER,TEXT,TEXT) TO accounting_app;
CREATE INDEX idx_case_collector ON accounting.case_tasks(firm_id,assignee_id,state,updated_at);

