-- compatibility: breaking
-- The six established checks remain mandatory; manual work adds one checked fact.
CREATE OR REPLACE FUNCTION accounting.rr_close_checks_valid(payload TEXT,target_book TEXT,target_status TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE checks JSONB; expected_manual TEXT;
BEGIN
 checks:=payload::jsonb;
 IF checks IS NULL OR jsonb_typeof(checks)<>'array' THEN RETURN false; END IF;
 IF jsonb_array_length(checks)<>7
 OR (SELECT count(DISTINCT c->>'code') FROM jsonb_array_elements(checks) c WHERE c->>'code' IN('unposted_periodic','unapproved_drafts','subledger_control','bank_reconciliation','trial_balance','report_checks','unposted_manual'))<>7
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE COALESCE(c->>'status','') NOT IN('pass','blocked'))
 OR (target_status IN('preflight_passed','reviewed','locked') AND EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE c->>'status'<>'pass'))
 OR (target_status='preflight_failed' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE c->>'status'='blocked')) THEN RETURN false; END IF;
 expected_manual:=CASE WHEN EXISTS(SELECT 1 FROM accounting.manual_review_requests m WHERE m.book_id=target_book AND NOT EXISTS(SELECT 1 FROM accounting.manual_review_withdrawals w WHERE w.request_id=m.id) AND NOT EXISTS(SELECT 1 FROM accounting.vouchers v JOIN accounting.voucher_review_completions done ON done.id=v.id WHERE v.review_request_id=m.id)) THEN 'blocked' ELSE 'pass' END;
 RETURN EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE c->>'code'='unposted_manual' AND c->>'status'=expected_manual);
END $$;
REVOKE ALL ON FUNCTION accounting.rr_close_checks_valid(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE OR REPLACE FUNCTION accounting.close_revision_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE run accounting.internal_close_runs; book accounting.books; ledger TEXT;
 allowed_fields TEXT[]; 
BEGIN
 SELECT * INTO book FROM accounting.books WHERE id=NEW.book_id;
 PERFORM accounting.lock_company_write(book.firm_id,book.company_id);
 IF NOT accounting.book_review_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Current close review permission required' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='internal_period_locks' THEN
  SELECT * INTO run FROM accounting.internal_close_runs WHERE id=NEW.close_run_id;
  SELECT ledger_id INTO ledger FROM accounting.ledger_periods WHERE book_id=NEW.book_id;
  IF run.id IS NULL OR NOT accounting.rr_close_checks_valid(run.checks_json,NEW.book_id,run.status) OR run.book_id<>NEW.book_id OR run.period<>NEW.period OR run.status<>'reviewed'
   OR NEW.period<>book.period OR NEW.firm_id<>book.firm_id OR NEW.company_id<>book.company_id OR NEW.ledger_id<>ledger
   OR NEW.locked_by IS DISTINCT FROM accounting.actor_user()
   OR run.reviewed_by IS NULL OR run.reviewed_by=run.created_by OR run.reviewed_at IS NULL
   OR NOT accounting.close_dependencies_match(NEW.book_id,run.dependencies_json)
   OR NOT accounting.close_report_binding_valid(NEW.book_id,NEW.period,run.report_run_id,run.dependencies_json) THEN
   RAISE EXCEPTION 'Current independently reviewed close dependencies required' USING ERRCODE='23514';
  END IF;
 ELSIF TG_OP='INSERT' THEN
  IF NEW.status NOT IN('preflight_passed','preflight_failed')
   OR NEW.created_by IS DISTINCT FROM accounting.actor_user()
   OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
   OR NEW.locked_by IS NOT NULL OR NEW.locked_at IS NOT NULL
   OR NEW.published_by IS NOT NULL OR NEW.published_at IS NOT NULL
   OR NEW.version<1 OR NEW.inputs_fingerprint !~ '^[0-9a-f]{64}$'
   OR NOT accounting.close_dependencies_match(NEW.book_id,NEW.dependencies_json)
   OR NOT accounting.close_report_binding_valid(NEW.book_id,NEW.period,NEW.report_run_id,NEW.dependencies_json)
   OR EXISTS(SELECT 1 FROM accounting.internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period) THEN
   RAISE EXCEPTION 'Current actor must create an unreviewed current preflight' USING ERRCODE='23514';
  END IF;
  IF NOT accounting.rr_close_checks_valid(NEW.checks_json,NEW.book_id,NEW.status) THEN
   RAISE EXCEPTION 'Close preflight checks must be complete and consistent' USING ERRCODE='23514';
  END IF;
 ELSE
  IF NEW.status=OLD.status THEN
   IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
    RAISE EXCEPTION 'Close preflight evidence and approvals are immutable' USING ERRCODE='23514';
   END IF;
   RETURN NEW;
  END IF;
  CASE
   WHEN OLD.status='preflight_passed' AND NEW.status='reviewed' THEN
    allowed_fields:=ARRAY['status','reviewed_by','reviewed_at'];
    IF NEW.reviewed_by IS DISTINCT FROM accounting.actor_user() OR NEW.created_by=accounting.actor_user()
     OR NEW.reviewed_at IS NULL THEN
     RAISE EXCEPTION 'Independent current close reviewer required' USING ERRCODE='42501';
    END IF;
   WHEN OLD.status='reviewed' AND NEW.status='locked' THEN
    allowed_fields:=ARRAY['status','locked_by','locked_at'];
    IF NEW.locked_by IS DISTINCT FROM accounting.actor_user() OR NEW.locked_at IS NULL
     OR NOT EXISTS(SELECT 1 FROM accounting.internal_period_locks l
       WHERE l.close_run_id=NEW.id AND l.book_id=NEW.book_id AND l.period=NEW.period
        AND l.locked_by=accounting.actor_user()) THEN
     RAISE EXCEPTION 'Matching current period lock required' USING ERRCODE='23514';
    END IF;
   WHEN OLD.status='locked' AND NEW.status='published' THEN
    allowed_fields:=ARRAY['status','published_by','published_at'];
    IF NEW.published_by IS DISTINCT FROM accounting.actor_user() OR NEW.published_at IS NULL
     OR NOT EXISTS(SELECT 1 FROM accounting.report_runs r WHERE r.id=NEW.report_run_id
       AND r.book_id=NEW.book_id AND r.period=NEW.period AND r.status='published') THEN
     RAISE EXCEPTION 'Published bound report required' USING ERRCODE='23514';
    END IF;
   ELSE RAISE EXCEPTION 'Invalid close status transition' USING ERRCODE='23514';
  END CASE;
  IF (to_jsonb(NEW)-allowed_fields) IS DISTINCT FROM (to_jsonb(OLD)-allowed_fields) THEN
   RAISE EXCEPTION 'Close preflight evidence and prior approvals are immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.status IN('reviewed','locked') AND (
   NOT accounting.rr_close_checks_valid(NEW.checks_json,NEW.book_id,NEW.status) OR
   NOT accounting.close_dependencies_match(NEW.book_id,NEW.dependencies_json)
   OR NOT accounting.close_report_binding_valid(NEW.book_id,NEW.period,NEW.report_run_id,NEW.dependencies_json)) THEN
   RAISE EXCEPTION 'Close dependencies changed; repeat preflight and review' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.close_revision_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
