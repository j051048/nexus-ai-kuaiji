-- Metadata capabilities reveal no internal case notes or original samples.
CREATE FUNCTION accounting.case_source_blocked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book)
   THEN RAISE EXCEPTION 'Source scope denied' USING ERRCODE='42501'; END IF;
  RETURN EXISTS(SELECT 1 FROM accounting.case_tasks c WHERE c.firm_id=accounting.actor_firm() AND c.state<>'closed' AND
   (c.source_id=target_source AND c.book_id=target_book OR c.event_id IN
    (SELECT event_id FROM accounting.business_mappings WHERE source_id=target_source AND book_id=target_book)));
 END
$$;
REVOKE ALL ON FUNCTION accounting.case_source_blocked(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.case_source_blocked(TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.event_superseded_attachments(target_event TEXT) RETURNS SETOF TEXT
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=accounting.actor_firm()
   AND accounting.business_event_visible(e.id,e.firm_id,e.company_id)) THEN
   RAISE EXCEPTION 'Event evidence scope denied' USING ERRCODE='42501'; END IF;
  RETURN QUERY SELECT s.old_attachment_id FROM accounting.case_supersessions s WHERE s.event_id=target_event AND s.firm_id=accounting.actor_firm();
 END
$$;
REVOKE ALL ON FUNCTION accounting.event_superseded_attachments(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.event_superseded_attachments(TEXT) TO accounting_app;

ALTER TABLE accounting.proposal_jobs ADD COLUMN result_rule_id TEXT;
ALTER TABLE accounting.proposal_jobs ADD COLUMN result_rule_version INTEGER;
CREATE UNIQUE INDEX idx_learning_rule_receipt_scope ON accounting.learning_rule_versions(rule_id,version,book_id);
ALTER TABLE accounting.proposal_jobs ADD CONSTRAINT proposal_company_rule_receipt_fk FOREIGN KEY(result_rule_id,result_rule_version,book_id) REFERENCES accounting.learning_rule_versions(rule_id,version,book_id);
ALTER TABLE accounting.proposal_jobs ADD CONSTRAINT proposal_company_rule_receipt_pair CHECK((result_rule_id IS NULL)=(result_rule_version IS NULL) AND (result_rule_id IS NULL OR result_template_id IS NULL));

CREATE FUNCTION accounting.proposal_rule_receipt_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF current_user='accounting_app' AND (NEW.result_rule_id IS NOT NULL OR NEW.result_rule_version IS NOT NULL) THEN
   RAISE EXCEPTION 'API cannot manufacture a company rule result' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END $$;
REVOKE ALL ON FUNCTION accounting.proposal_rule_receipt_guard() FROM PUBLIC;
CREATE TRIGGER proposal_rule_receipt_identity BEFORE INSERT OR UPDATE ON accounting.proposal_jobs
 FOR EACH ROW EXECUTE FUNCTION accounting.proposal_rule_receipt_guard();

CREATE FUNCTION accounting.worker_finish_proposal_company_rule(target_job TEXT,target_token TEXT,target_rule TEXT,target_version INTEGER,target_draft_version INTEGER) RETURNS BOOLEAN
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
    jsonb_build_object('lines',(SELECT jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit',l.debit_cents,'credit',l.credit_cents,
     'aux',COALESCE(a.values_json::jsonb,'{}'::jsonb)) ORDER BY l.ordinal)
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
REVOKE ALL ON FUNCTION accounting.worker_finish_proposal_company_rule(TEXT,TEXT,TEXT,INTEGER,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.worker_finish_proposal_company_rule(TEXT,TEXT,TEXT,INTEGER,INTEGER) TO accounting_worker;

CREATE INDEX idx_case_blocking_source ON accounting.case_tasks(firm_id,source_id,state);
CREATE INDEX idx_case_blocking_event ON accounting.case_tasks(firm_id,event_id,state);
