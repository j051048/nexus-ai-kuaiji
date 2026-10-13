CREATE TABLE accounting.bank_import_keys (
 source_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 namespace TEXT NOT NULL CHECK(length(btrim(namespace)) BETWEEN 1 AND 100),
 bank_account TEXT NOT NULL CHECK(length(btrim(bank_account)) BETWEEN 1 AND 100),
 stable_transaction_id TEXT NOT NULL CHECK(length(btrim(stable_transaction_id)) BETWEEN 1 AND 200),
 original_external_id TEXT NOT NULL,content_fingerprint TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(firm_id,company_id,namespace,bank_account,stable_transaction_id),
 FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id),
 FOREIGN KEY(book_id,firm_id) REFERENCES accounting.books(id,firm_id)
);
CREATE TABLE accounting.bank_duplicate_decisions (
 source_id TEXT NOT NULL,other_source_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL,other_fingerprint TEXT NOT NULL,expected_draft_version INTEGER NOT NULL,
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 reviewed_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(source_id,other_source_id),CHECK(source_id<other_source_id),
 FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id),
 FOREIGN KEY(other_source_id,book_id) REFERENCES accounting.sources(id,book_id),
 FOREIGN KEY(book_id,firm_id) REFERENCES accounting.books(id,firm_id)
);
CREATE INDEX idx_bank_import_account ON accounting.bank_import_keys(book_id,namespace,bank_account,source_id);
CREATE INDEX idx_sources_duplicate_facts ON accounting.sources(book_id,source_kind,date,amount_cents,direction,counterparty);
CREATE FUNCTION accounting.bank_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Bank identity and duplicate reviews are immutable' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='bank_import_keys' THEN
   IF NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.books b ON b.id=s.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id
    AND s.source_kind='bank' AND s.external_id='bank:v1:'||NEW.source_id AND s.fingerprint=NEW.content_fingerprint
    AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND p.status='open' AND substr(s.date,1,7)=b.period) THEN RAISE EXCEPTION 'Bank identity scope mismatch' USING ERRCODE='23514'; END IF;
  ELSE
   IF NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.sources o ON o.id=NEW.other_source_id AND o.book_id=s.book_id
    JOIN accounting.bank_import_keys k ON k.source_id=s.id JOIN accounting.bank_import_keys ko ON ko.source_id=o.id
    JOIN accounting.periods p ON p.book_id=s.book_id JOIN accounting.books b ON b.id=s.book_id AND p.period=b.period
    WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND b.firm_id=NEW.firm_id AND p.status='open'
     AND s.fingerprint=NEW.source_fingerprint AND o.fingerprint=NEW.other_fingerprint
     AND k.bank_account=ko.bank_account AND s.date=o.date AND s.direction=o.direction
     AND s.counterparty=o.counterparty AND s.amount_cents=o.amount_cents
     AND EXISTS(SELECT 1 FROM accounting.drafts d WHERE d.source_id IN (s.id,o.id) AND d.book_id=s.book_id AND d.status='draft' AND d.version=NEW.expected_draft_version))
    THEN RAISE EXCEPTION 'Duplicate decision must use current canonical bank facts' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END $$;
REVOKE ALL ON FUNCTION accounting.bank_identity_guard() FROM PUBLIC;
CREATE TRIGGER bank_import_identity BEFORE INSERT OR UPDATE OR DELETE ON accounting.bank_import_keys FOR EACH ROW EXECUTE FUNCTION accounting.bank_identity_guard();
CREATE TRIGGER bank_duplicate_review BEFORE INSERT OR UPDATE OR DELETE ON accounting.bank_duplicate_decisions FOR EACH ROW EXECUTE FUNCTION accounting.bank_identity_guard();
ALTER TABLE accounting.bank_import_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_import_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_duplicate_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_duplicate_decisions FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT ON accounting.bank_import_keys,accounting.bank_duplicate_decisions TO accounting_app;
CREATE POLICY bank_import_read ON accounting.bank_import_keys FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY bank_import_create ON accounting.bank_import_keys FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.book_allowed(book_id,true));
CREATE POLICY bank_duplicate_read ON accounting.bank_duplicate_decisions FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY bank_duplicate_review ON accounting.bank_duplicate_decisions FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND reviewed_by=accounting.actor_user() AND accounting.ai_book_reviewer(book_id));

CREATE FUNCTION accounting.bank_source_import_blocked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book)
   THEN RAISE EXCEPTION 'Bank source scope denied' USING ERRCODE='42501'; END IF;
  RETURN EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.bank_import_keys k ON k.source_id=s.id
   JOIN accounting.bank_import_keys ko ON ko.book_id=k.book_id AND ko.bank_account=k.bank_account AND ko.source_id<>k.source_id
   JOIN accounting.sources o ON o.id=ko.source_id AND o.book_id=s.book_id
   WHERE s.id=target_source AND s.book_id=target_book AND k.firm_id=accounting.actor_firm()
    AND s.date=o.date AND s.direction=o.direction AND s.amount_cents=o.amount_cents AND s.counterparty=o.counterparty
    AND NOT EXISTS(SELECT 1 FROM accounting.bank_duplicate_decisions d WHERE d.book_id=target_book
     AND d.source_id=least(s.id,o.id) AND d.other_source_id=greatest(s.id,o.id)));
 END $$;
REVOKE ALL ON FUNCTION accounting.bank_source_import_blocked(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.bank_source_import_blocked(TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.bank_identity_elsewhere(target_book TEXT,target_namespace TEXT,target_account TEXT,target_transaction TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.book_allowed(target_book,true) THEN RAISE EXCEPTION 'Bank identity scope denied' USING ERRCODE='42501'; END IF;
  RETURN EXISTS(SELECT 1 FROM accounting.bank_import_keys k JOIN accounting.books b ON b.firm_id=k.firm_id AND b.company_id=k.company_id
   WHERE b.id=target_book AND k.book_id<>target_book AND k.namespace=target_namespace AND k.bank_account=target_account AND k.stable_transaction_id=target_transaction);
 END $$;
REVOKE ALL ON FUNCTION accounting.bank_identity_elsewhere(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.bank_identity_elsewhere(TEXT,TEXT,TEXT,TEXT) TO accounting_app;

-- Correct the native company-rule receipt renderer; 031 remains immutable.
CREATE OR REPLACE FUNCTION accounting.worker_finish_proposal_company_rule(target_job TEXT,target_token TEXT,target_rule TEXT,target_version INTEGER,target_draft_version INTEGER) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT;
 BEGIN
  IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') THEN RAISE EXCEPTION 'Worker scope denied' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND status='running' AND lease_token=target_token AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL OR j.current_run_id IS NOT NULL OR target_draft_version<>j.expected_draft_version+1
   OR NOT accounting.book_allowed(j.book_id,true) OR NOT accounting.ai_event_open(j.event_id,j.firm_id,j.company_id) THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version
   JOIN accounting.drafts d ON d.id=j.draft_id AND d.book_id=j.book_id JOIN accounting.business_events e ON e.id=j.event_id
   WHERE r.id=target_rule AND r.book_id=j.book_id AND r.firm_id=j.firm_id AND r.company_id=j.company_id AND r.status='published' AND r.current_version=target_version
   AND d.status='draft' AND d.version=target_draft_version AND d.last_manual_edit_version=0 AND e.fact_version=j.expected_fact_version
   AND accounting.learning_matches(v.definition_json::jsonb,jsonb_build_object('source_kind','business','business_type',e.template_id,'payload',e.payload::jsonb))
   AND accounting.learning_render_matches(j.book_id,v.definition_json::jsonb,
    jsonb_build_object('source_kind','business','business_type',e.template_id,'payload',e.payload::jsonb,
     'context',jsonb_build_object('department_id',e.department_id,'applicant_id',e.applicant_id),
     'recognized',accounting.learning_recognized(e.id,e.fact_version,(SELECT amount_cents FROM accounting.sources WHERE id=d.source_id AND book_id=j.book_id))),
    jsonb_build_object('lines',(SELECT jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit',(l.debit_cents/100)::text||'.'||lpad((l.debit_cents%100)::text,2,'0'),'credit',(l.credit_cents/100)::text||'.'||lpad((l.credit_cents%100)::text,2,'0'),
     'auxiliary_values',COALESCE(a.values_json::jsonb,'{}'::jsonb)) ORDER BY l.ordinal)
     FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal
     WHERE l.draft_id=j.draft_id AND l.book_id=j.book_id)))
   AND NOT accounting.case_source_blocked(j.book_id,d.source_id)) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.proposal_attempts SET phase='finished',status='succeeded',error_code='',error_detail='',finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.proposal_jobs SET status='succeeded',lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,finished_at=stamp,error_code='',error_detail='',
   result_rule_id=target_rule,result_rule_version=target_version,result_draft_version=target_draft_version WHERE id=j.id;
  RETURN true;
 END
$$;
