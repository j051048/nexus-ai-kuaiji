-- Current mapping and immutable mapping versions are separate. Historical jobs
-- keep their original draft scopes; no input is updated by cascading a revision.
CREATE TABLE accounting.business_mapping_versions (LIKE accounting.business_mappings INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
ALTER TABLE accounting.business_mapping_versions ADD PRIMARY KEY(draft_id),
 ADD UNIQUE(event_id,firm_id,company_id,book_id,draft_id),ADD UNIQUE(source_id),
 ADD FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 ADD FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 ADD FOREIGN KEY(draft_id,source_id,book_id) REFERENCES accounting.drafts(id,source_id,book_id);
INSERT INTO accounting.business_mapping_versions SELECT * FROM accounting.business_mappings;
DO $$ DECLARE item RECORD; BEGIN
 FOR item IN SELECT conrelid::regclass AS relation,conname FROM pg_constraint
  WHERE contype='f' AND confrelid='accounting.business_mappings'::regclass LOOP
  EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',item.relation,item.conname);
 END LOOP;
END $$;
ALTER TABLE accounting.ai_proposal_applications ADD FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id)
 REFERENCES accounting.business_mapping_versions(event_id,firm_id,company_id,book_id,draft_id);
ALTER TABLE accounting.proposal_jobs ADD FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id)
 REFERENCES accounting.business_mapping_versions(event_id,firm_id,company_id,book_id,draft_id);
-- Bank links retain original event/book/source FKs and the current-mapping
-- insertion guard. Linked events must be manually unlinked before replacement.
ALTER TABLE accounting.ai_bank_links ADD FOREIGN KEY(event_id,firm_id,company_id)
 REFERENCES accounting.business_events(id,firm_id,company_id);
ALTER TABLE accounting.business_mapping_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.business_mapping_versions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.business_mapping_versions FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT ON accounting.business_mapping_versions TO accounting_app;
CREATE POLICY mapping_versions_read ON accounting.business_mapping_versions FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE TRIGGER mapping_version_immutable BEFORE UPDATE OR DELETE ON accounting.business_mapping_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.capture_mapping_version() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 INSERT INTO accounting.business_mapping_versions VALUES(NEW.event_id,NEW.firm_id,NEW.company_id,NEW.book_id,NEW.source_id,NEW.draft_id,
  NEW.submitted_version,NEW.idempotency_key,NEW.request_fingerprint,NEW.created_by,NEW.created_at);
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.capture_mapping_version() FROM PUBLIC;
CREATE TRIGGER mapping_version_capture AFTER INSERT ON accounting.business_mappings
 FOR EACH ROW EXECUTE FUNCTION accounting.capture_mapping_version();

CREATE TABLE accounting.fact_revisions(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 old_book_id TEXT NOT NULL,old_source_id TEXT NOT NULL,old_draft_id TEXT NOT NULL,target_book_id TEXT NOT NULL,
 expected_event_version INTEGER NOT NULL CHECK(expected_event_version>0),expected_fact_version INTEGER NOT NULL CHECK(expected_fact_version>0),
 expected_draft_version INTEGER NOT NULL CHECK(expected_draft_version>0),old_snapshot TEXT NOT NULL CHECK(jsonb_typeof(old_snapshot::jsonb)='object'),
 proposed_payload TEXT NOT NULL CHECK(jsonb_typeof(proposed_payload::jsonb)='object'),reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 proposed_by TEXT NOT NULL REFERENCES accounting.users(id),idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),state TEXT NOT NULL CHECK(state IN('pending','rejected','replaced','adjustment_proposed')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,
 UNIQUE(firm_id,proposed_by,idempotency_key),UNIQUE(id,firm_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(old_book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(target_book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(old_draft_id,old_source_id,old_book_id) REFERENCES accounting.drafts(id,source_id,book_id)
);
CREATE UNIQUE INDEX idx_revision_pending ON accounting.fact_revisions(event_id) WHERE state='pending';
CREATE INDEX idx_revision_page ON accounting.fact_revisions(firm_id,old_book_id,target_book_id,created_at,id);
CREATE TABLE accounting.fact_revision_decisions(
 id TEXT PRIMARY KEY,revision_id TEXT NOT NULL,firm_id TEXT NOT NULL,decision TEXT NOT NULL CHECK(decision IN('accept','reject')),
 result_state TEXT NOT NULL CHECK(result_state IN('rejected','replaced','adjustment_proposed')),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),reviewed_by TEXT NOT NULL REFERENCES accounting.users(id),
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,
 UNIQUE(revision_id),UNIQUE(firm_id,reviewed_by,idempotency_key),FOREIGN KEY(revision_id,firm_id) REFERENCES accounting.fact_revisions(id,firm_id)
);
CREATE TABLE accounting.fact_revision_lineage(
 revision_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 old_book_id TEXT NOT NULL,old_source_id TEXT NOT NULL UNIQUE,old_draft_id TEXT NOT NULL UNIQUE,
 new_book_id TEXT NOT NULL,new_source_id TEXT NOT NULL UNIQUE,new_draft_id TEXT NOT NULL UNIQUE,
 old_fact_version INTEGER NOT NULL,new_fact_version INTEGER NOT NULL CHECK(new_fact_version=old_fact_version+1),created_at TEXT NOT NULL,
 FOREIGN KEY(revision_id,firm_id) REFERENCES accounting.fact_revisions(id,firm_id),
 FOREIGN KEY(old_draft_id,old_source_id,old_book_id) REFERENCES accounting.drafts(id,source_id,book_id),
 FOREIGN KEY(new_draft_id,new_source_id,new_book_id) REFERENCES accounting.drafts(id,source_id,book_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id)
);
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['fact_revisions','fact_revision_decisions','fact_revision_lineage'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',item);
  EXECUTE format('GRANT SELECT ON accounting.%I TO accounting_app',item);
 END LOOP;
END $$;
CREATE POLICY fact_revision_read ON accounting.fact_revisions FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(old_book_id) AND accounting.book_allowed(target_book_id));
CREATE POLICY revision_decision_read ON accounting.fact_revision_decisions FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND EXISTS(SELECT 1 FROM accounting.fact_revisions r WHERE r.id=revision_id));
CREATE POLICY revision_lineage_read ON accounting.fact_revision_lineage FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.book_allowed(old_book_id) OR accounting.book_allowed(new_book_id)));
CREATE TRIGGER revision_decisions_immutable BEFORE UPDATE OR DELETE ON accounting.fact_revision_decisions
 FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER revision_lineage_immutable BEFORE UPDATE OR DELETE ON accounting.fact_revision_lineage
 FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER revisions_no_delete BEFORE DELETE ON accounting.fact_revisions
 FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();

CREATE FUNCTION accounting.revision_source_inactive(target_book TEXT,target_source TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE book_id=target_book AND id=target_source) THEN
  RAISE EXCEPTION 'Revision source scope denied' USING ERRCODE='42501'; END IF;
 RETURN EXISTS(SELECT 1 FROM accounting.fact_revision_lineage WHERE old_book_id=target_book AND old_source_id=target_source);
 END $$;
REVOKE ALL ON FUNCTION accounting.revision_source_inactive(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.revision_source_inactive(TEXT,TEXT) TO accounting_app;
CREATE FUNCTION accounting.revision_source_blocked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE book_id=target_book AND id=target_source) THEN
  RAISE EXCEPTION 'Revision source scope denied' USING ERRCODE='42501'; END IF;
 RETURN EXISTS(SELECT 1 FROM accounting.fact_revision_lineage WHERE old_book_id=target_book AND old_source_id=target_source)
  OR EXISTS(SELECT 1 FROM accounting.fact_revisions WHERE old_book_id=target_book AND old_source_id=target_source AND state='pending');
 END $$;
REVOKE ALL ON FUNCTION accounting.revision_source_blocked(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.revision_source_blocked(TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.submit_fact_revision(target_event TEXT,target_book TEXT,event_version INTEGER,fact_version INTEGER,draft_version INTEGER,
 new_payload TEXT,target_reason TEXT,target_key TEXT,target_fingerprint TEXT,target_id TEXT) RETURNS TEXT
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE e accounting.business_events;m accounting.business_mappings;d accounting.drafts;b accounting.books; prior accounting.fact_revisions;p JSONB;old_snapshot JSONB; stamp TEXT;
 BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO e FROM accounting.business_events WHERE id=target_event AND firm_id=accounting.actor_firm();
 SELECT * INTO m FROM accounting.business_mappings WHERE event_id=target_event;
 SELECT * INTO b FROM accounting.books WHERE id=target_book;
 IF pg_has_role(session_user,'accounting_worker','MEMBER') OR NOT accounting.actor_active() OR e.id IS NULL OR e.status<>'handed_off'
  OR m.event_id IS NULL OR NOT accounting.book_allowed(m.book_id,true) OR NOT accounting.book_allowed(target_book,true)
  OR b.firm_id<>e.firm_id OR b.company_id<>e.company_id THEN
  RAISE EXCEPTION 'Current event and same-company editor scopes required' USING ERRCODE='42501'; END IF;
 IF target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 3 AND 1000 OR target_key IS NULL OR length(target_key) NOT BETWEEN 8 AND 120
  OR target_fingerprint IS NULL OR target_fingerprint!~'^[a-f0-9]{64}$' OR target_id IS NULL OR length(target_id) NOT BETWEEN 8 AND 100 THEN
  RAISE EXCEPTION 'Bounded revision command required' USING ERRCODE='23514'; END IF;
 SELECT * INTO prior FROM accounting.fact_revisions WHERE firm_id=e.firm_id AND proposed_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN
  IF prior.event_id<>target_event OR prior.target_book_id<>target_book OR prior.proposed_payload::jsonb<>new_payload::jsonb
   OR prior.expected_event_version<>event_version OR prior.expected_fact_version<>fact_version OR prior.expected_draft_version<>draft_version
   OR prior.reason<>target_reason OR prior.request_fingerprint<>target_fingerprint THEN
   RAISE EXCEPTION 'Revision key already binds another command' USING ERRCODE='23514'; END IF;
  RETURN prior.id;
 END IF;
 SELECT * INTO d FROM accounting.drafts WHERE id=m.draft_id AND book_id=m.book_id;
 IF e.version IS DISTINCT FROM event_version OR e.fact_version IS DISTINCT FROM fact_version OR d.version IS DISTINCT FROM draft_version
  OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=m.book_id AND status='open')
  OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=target_book AND status='open') THEN
  RAISE EXCEPTION 'Current versions and open periods required' USING ERRCODE='23514'; END IF;
 p:=new_payload::jsonb;
 IF jsonb_typeof(p)<>'object' OR p=e.payload::jsonb
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(p) AS keys(field) WHERE field NOT IN('occurred_on','description','counterparty','amount','direction','payment_status','category','purpose','project'))
  OR EXISTS(SELECT 1 FROM jsonb_each(p) AS fields(field,value) WHERE jsonb_typeof(value)<>'string')
  OR length(COALESCE(p->>'purpose',''))>1000 OR length(COALESCE(p->>'project',''))>200
  OR p->>'occurred_on' IS NULL OR p->>'occurred_on'!~'^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
  OR to_char((p->>'occurred_on')::DATE,'YYYY-MM-DD')<>p->>'occurred_on' OR left(p->>'occurred_on',7)<>b.period
  OR p->>'amount' IS NULL OR p->>'amount'!~'^(0|[1-9][0-9]{0,11})\.[0-9]{2}$' OR (p->>'amount')::NUMERIC<=0
  OR p->>'direction' IS NULL OR p->>'direction' NOT IN('expense','income') OR p->>'payment_status' IS NULL OR p->>'payment_status' NOT IN('paid','unpaid')
  OR p->>'category' IS NULL OR p->>'category' NOT IN('office','travel','rent','communication','other')
  OR p->>'description' IS NULL OR length(p->>'description') NOT BETWEEN 1 AND 500
  OR p->>'counterparty' IS NULL OR length(p->>'counterparty') NOT BETWEEN 1 AND 200
  OR (e.template_id IN('reimbursement','procurement_expense') AND p->>'direction'<>'expense')
  OR (e.template_id='reimbursement' AND p->>'payment_status'<>'unpaid') OR (e.template_id='daily_cashflow' AND p->>'payment_status'<>'paid') THEN
  RAISE EXCEPTION 'Valid exact business facts and target period required' USING ERRCODE='23514'; END IF;
 stamp:=accounting.ocr_utc_now();
 SELECT jsonb_build_object('payload',e.payload::jsonb,'event_version',e.version,'fact_version',e.fact_version,'draft_version',d.version,
  'draft_status',d.status,'mapping',to_jsonb(m),'attachments',COALESCE(jsonb_agg(jsonb_build_object('id',a.id,'filename',a.filename,'media_type',a.media_type,'sha256',a.sha256,'size_bytes',a.size_bytes,'created_at',a.created_at,'created_by',a.created_by) ORDER BY a.created_at,a.id) FILTER(WHERE a.id IS NOT NULL),'[]'::jsonb))
 INTO old_snapshot FROM accounting.business_attachments a WHERE a.event_id=e.id;
 INSERT INTO accounting.fact_revisions(id,firm_id,company_id,event_id,old_book_id,old_source_id,old_draft_id,target_book_id,
  expected_event_version,expected_fact_version,expected_draft_version,old_snapshot,proposed_payload,reason,proposed_by,idempotency_key,request_fingerprint,state,version,created_at)
 VALUES(target_id,e.firm_id,e.company_id,e.id,m.book_id,m.source_id,m.draft_id,target_book,event_version,fact_version,draft_version,
  old_snapshot::TEXT,p::TEXT,target_reason,accounting.actor_user(),target_key,target_fingerprint,'pending',1,stamp);
 INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
 VALUES(e.firm_id,m.book_id,accounting.actor_user(),'business.fact_revision.submit',target_id,
  jsonb_build_object('event_id',e.id,'target_book_id',target_book,'reason',target_reason)::TEXT,stamp);
 RETURN target_id;
 END $$;
REVOKE ALL ON FUNCTION accounting.submit_fact_revision(TEXT,TEXT,INTEGER,INTEGER,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.submit_fact_revision(TEXT,TEXT,INTEGER,INTEGER,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.review_fact_revision(target_revision TEXT,target_version INTEGER,target_decision TEXT,target_reason TEXT,target_key TEXT,target_fingerprint TEXT) RETURNS TEXT
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE r accounting.fact_revisions;e accounting.business_events;m accounting.business_mappings;d accounting.drafts;prior accounting.fact_revision_decisions;
 result TEXT;stamp TEXT;sid TEXT;did TEXT;payload JSONB;evidence TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO r FROM accounting.fact_revisions WHERE id=target_revision AND firm_id=accounting.actor_firm() FOR UPDATE;
 SELECT * INTO e FROM accounting.business_events WHERE id=r.event_id;
 IF pg_has_role(session_user,'accounting_worker','MEMBER') OR NOT accounting.actor_active() OR r.id IS NULL
  OR NOT accounting.ai_book_reviewer(r.old_book_id) OR NOT accounting.ai_book_reviewer(r.target_book_id)
  OR e.applicant_id=accounting.actor_user() OR r.proposed_by=accounting.actor_user() THEN
  RAISE EXCEPTION 'Independent reviewer for both current books required' USING ERRCODE='42501'; END IF;
 IF target_decision IS NULL OR target_decision NOT IN('accept','reject') OR target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 3 AND 1000
  OR target_key IS NULL OR length(target_key) NOT BETWEEN 8 AND 120 OR target_fingerprint IS NULL OR target_fingerprint!~'^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION 'Bounded independent review command required' USING ERRCODE='23514'; END IF;
 SELECT * INTO prior FROM accounting.fact_revision_decisions WHERE firm_id=r.firm_id AND reviewed_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN
  IF prior.revision_id<>r.id OR prior.decision<>target_decision OR prior.reason<>target_reason OR prior.request_fingerprint<>target_fingerprint THEN
   RAISE EXCEPTION 'Review key already binds another command' USING ERRCODE='23514'; END IF; RETURN prior.result_state;
 END IF;
 IF r.state<>'pending' OR r.version IS DISTINCT FROM target_version THEN RAISE EXCEPTION 'Current pending revision version required' USING ERRCODE='23514'; END IF;
 SELECT * INTO m FROM accounting.business_mappings WHERE event_id=e.id;
 SELECT * INTO d FROM accounting.drafts WHERE id=m.draft_id AND book_id=m.book_id FOR UPDATE;
 IF target_decision='accept' AND ((m.book_id,m.source_id,m.draft_id,e.version,e.fact_version,d.version)
  IS DISTINCT FROM (r.old_book_id,r.old_source_id,r.old_draft_id,r.expected_event_version,r.expected_fact_version,r.expected_draft_version)
  OR e.payload::jsonb IS DISTINCT FROM r.old_snapshot::jsonb->'payload'
  OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=r.old_book_id AND status='open')
  OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=r.target_book_id AND status='open')) THEN
  RAISE EXCEPTION 'Original facts, current mappings and open periods required' USING ERRCODE='23514'; END IF;
 IF target_decision='accept' AND d.status='draft' AND EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE event_id=e.id) THEN
  RAISE EXCEPTION 'Existing bank links must be manually reconciled and removed before replacement' USING ERRCODE='23514'; END IF;
 result:=CASE WHEN target_decision='reject' THEN 'rejected' WHEN d.status='draft' THEN 'replaced' ELSE 'adjustment_proposed' END;
 stamp:=accounting.ocr_utc_now();
 INSERT INTO accounting.fact_revision_decisions VALUES(md5(clock_timestamp()::TEXT||random()::TEXT),r.id,r.firm_id,target_decision,result,
  target_reason,accounting.actor_user(),target_key,target_fingerprint,stamp);
 IF result='replaced' THEN
  payload:=r.proposed_payload::jsonb;evidence:='revision:'||r.id;
  sid:=md5(clock_timestamp()::TEXT||random()::TEXT||r.id||'source');did:=md5(clock_timestamp()::TEXT||random()::TEXT||r.id||'draft');
  INSERT INTO accounting.sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind)
  VALUES(sid,r.target_book_id,evidence,'经独立审批的业务事实修订',payload->>'occurred_on',payload->>'description',payload->>'counterparty',
   ((payload->>'amount')::NUMERIC*100)::BIGINT,payload->>'direction',evidence,md5(payload::TEXT||evidence)||md5(evidence||payload::TEXT),'business');
  INSERT INTO accounting.drafts(id,book_id,source_id,description,status,version,explanation)
   VALUES(did,r.target_book_id,sid,payload->>'description','draft',1,'业务事实已独立审批替代；空草稿等待后台自动生成及会计复核。');
  INSERT INTO accounting.business_mapping_versions VALUES(e.id,e.firm_id,e.company_id,r.target_book_id,sid,did,e.submitted_version,
   evidence,r.request_fingerprint,accounting.actor_user(),stamp);
  INSERT INTO accounting.fact_revision_lineage VALUES(r.id,e.firm_id,e.company_id,e.id,m.book_id,m.source_id,m.draft_id,
   r.target_book_id,sid,did,e.fact_version,e.fact_version+1,stamp);
  UPDATE accounting.business_mappings SET book_id=r.target_book_id,source_id=sid,draft_id=did,idempotency_key=evidence,
   request_fingerprint=r.request_fingerprint,created_by=accounting.actor_user(),created_at=stamp WHERE event_id=e.id;
  UPDATE accounting.business_events SET payload=r.proposed_payload,version=version+1,fact_version=fact_version+1,updated_at=stamp WHERE id=e.id;
 END IF;
 UPDATE accounting.fact_revisions SET state=result,version=version+1 WHERE id=r.id;
 INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
 VALUES(r.firm_id,r.old_book_id,accounting.actor_user(),'business.fact_revision.'||result,r.id,
  jsonb_build_object('reason',target_reason,'event_id',e.id,'target_book_id',r.target_book_id)::TEXT,stamp);
 RETURN result; END $$;
REVOKE ALL ON FUNCTION accounting.review_fact_revision(TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.review_fact_revision(TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.revision_mapping_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.fact_revision_lineage l JOIN accounting.fact_revisions r ON r.id=l.revision_id
  JOIN accounting.fact_revision_decisions x ON x.revision_id=r.id
  WHERE l.event_id=OLD.event_id AND l.old_book_id=OLD.book_id AND l.old_source_id=OLD.source_id AND l.old_draft_id=OLD.draft_id
   AND l.new_book_id=NEW.book_id AND l.new_source_id=NEW.source_id AND l.new_draft_id=NEW.draft_id
   AND r.state='pending' AND x.result_state='replaced' AND x.reviewed_by=accounting.actor_user()
   AND (NEW.event_id,NEW.firm_id,NEW.company_id,NEW.submitted_version) IS NOT DISTINCT FROM (OLD.event_id,OLD.firm_id,OLD.company_id,OLD.submitted_version)) THEN
  RAISE EXCEPTION 'Only independently approved fact replacement may change current mapping' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.revision_mapping_guard() FROM PUBLIC;
CREATE TRIGGER revision_mapping_guard BEFORE UPDATE ON accounting.business_mappings FOR EACH ROW EXECUTE FUNCTION accounting.revision_mapping_guard();

CREATE FUNCTION accounting.revision_old_draft_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE target_draft TEXT;target_book TEXT; BEGIN
 IF TG_TABLE_NAME='drafts' THEN target_draft:=OLD.id;target_book:=OLD.book_id;
 ELSE target_draft:=CASE WHEN TG_OP='DELETE' THEN OLD.draft_id ELSE NEW.draft_id END;
  target_book:=CASE WHEN TG_OP='DELETE' THEN OLD.book_id ELSE NEW.book_id END; END IF;
 IF TG_TABLE_NAME='draft_lines' AND TG_OP='UPDATE' AND EXISTS(
  SELECT 1 FROM accounting.fact_revision_lineage WHERE old_draft_id=OLD.draft_id AND old_book_id=OLD.book_id) THEN
  RAISE EXCEPTION 'Superseded draft lines cannot move to another draft' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM accounting.fact_revision_lineage WHERE old_draft_id=target_draft AND old_book_id=target_book) THEN
  RAISE EXCEPTION 'Superseded draft and original lines remain frozen' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='drafts' AND TG_OP='UPDATE' AND NEW.status IN('approved','exported') AND EXISTS(
  SELECT 1 FROM accounting.fact_revisions WHERE old_draft_id=target_draft AND old_book_id=target_book AND state='pending') THEN
  RAISE EXCEPTION 'Pending fact revision requires independent review first' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.revision_old_draft_guard() FROM PUBLIC;
CREATE TRIGGER revision_old_draft_guard BEFORE UPDATE OR DELETE ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.revision_old_draft_guard();
CREATE TRIGGER revision_old_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.revision_old_draft_guard();

CREATE FUNCTION accounting.revision_immutable_input_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.old_book_id,NEW.old_source_id,NEW.old_draft_id,NEW.target_book_id,
  NEW.expected_event_version,NEW.expected_fact_version,NEW.expected_draft_version,NEW.old_snapshot,NEW.proposed_payload,NEW.reason,NEW.proposed_by,NEW.idempotency_key,NEW.request_fingerprint,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.old_book_id,OLD.old_source_id,OLD.old_draft_id,OLD.target_book_id,
  OLD.expected_event_version,OLD.expected_fact_version,OLD.expected_draft_version,OLD.old_snapshot,OLD.proposed_payload,OLD.reason,OLD.proposed_by,OLD.idempotency_key,OLD.request_fingerprint,OLD.created_at)
  OR OLD.state<>'pending' OR NEW.version<>OLD.version+1 OR NOT EXISTS(
   SELECT 1 FROM accounting.fact_revision_decisions x WHERE x.revision_id=OLD.id AND x.result_state=NEW.state AND x.reviewed_by=accounting.actor_user()) THEN
  RAISE EXCEPTION 'Revision inputs and final review history are immutable' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.revision_immutable_input_guard() FROM PUBLIC;
CREATE TRIGGER revision_immutable_input_guard BEFORE UPDATE ON accounting.fact_revisions FOR EACH ROW EXECUTE FUNCTION accounting.revision_immutable_input_guard();

CREATE FUNCTION accounting.revision_business_payload_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF OLD.status='handed_off' AND NEW.status<>'handed_off' THEN
  RAISE EXCEPTION 'Handed-off business status remains frozen' USING ERRCODE='23514'; END IF;
 IF OLD.status NOT IN('draft','returned') AND NEW.payload IS DISTINCT FROM OLD.payload AND NOT EXISTS(
  SELECT 1 FROM accounting.fact_revisions r JOIN accounting.fact_revision_lineage l ON l.revision_id=r.id
   JOIN accounting.fact_revision_decisions x ON x.revision_id=r.id JOIN accounting.business_mappings m ON m.event_id=r.event_id
  WHERE r.event_id=OLD.id AND r.state='pending' AND x.result_state='replaced' AND x.reviewed_by=accounting.actor_user()
   AND r.proposed_payload::jsonb=NEW.payload::jsonb AND r.expected_event_version=OLD.version
   AND r.expected_fact_version=OLD.fact_version AND l.new_fact_version=NEW.fact_version
   AND m.book_id=l.new_book_id AND m.source_id=l.new_source_id AND m.draft_id=l.new_draft_id) THEN
  RAISE EXCEPTION 'Approved business facts require an independent revision' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.revision_business_payload_guard() FROM PUBLIC;
CREATE TRIGGER revision_business_payload_guard BEFORE UPDATE ON accounting.business_events FOR EACH ROW EXECUTE FUNCTION accounting.revision_business_payload_guard();

CREATE OR REPLACE FUNCTION accounting.business_row_visible(target_event TEXT,target_firm TEXT,target_company TEXT,target_department TEXT,target_applicant TEXT,target_status TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active') AND (
  (accounting.business_member(target_firm,target_company) AND target_applicant=accounting.actor_user())
  OR accounting.business_finance(target_firm,target_company)
  OR accounting.business_department_approver(target_firm,target_company,target_department)
  OR (target_status='handed_off' AND EXISTS(SELECT 1 FROM accounting.business_mapping_versions m
   WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company AND accounting.book_allowed(m.book_id))))
 $$;
