-- compatibility: breaking
-- PLT04: unresolved fee variances may be advisory when the active policy says so.
CREATE OR REPLACE FUNCTION accounting.rr_close_checks_valid(payload TEXT,target_book TEXT,target_status TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE checks JSONB; expected_manual TEXT;
BEGIN
 checks:=payload::jsonb;
 IF checks IS NULL OR jsonb_typeof(checks)<>'array' THEN RETURN false; END IF;
 IF jsonb_array_length(checks)<>8
 OR (SELECT count(DISTINCT c->>'code') FROM jsonb_array_elements(checks) c WHERE c->>'code' IN('unposted_periodic','unapproved_drafts','subledger_control','bank_reconciliation','trial_balance','report_checks','unposted_manual','fee_variance_pending'))<>8
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE COALESCE(c->>'status','') NOT IN('pass','blocked'))
 OR (target_status IN('preflight_passed','reviewed','locked') AND EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE c->>'status'<>'pass'))
 OR (target_status='preflight_failed' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE c->>'status'='blocked')) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE c->>'code'='fee_variance_pending' AND c->>'status'=CASE WHEN EXISTS(SELECT 1 FROM accounting.fee_receivable_variances v WHERE v.book_id=target_book AND v.period=(SELECT period FROM accounting.books b WHERE b.id=target_book) AND v.status='pending') AND NOT EXISTS(SELECT 1 FROM accounting.accounting_policies p JOIN accounting.ledger_periods lp ON lp.ledger_id=p.ledger_id WHERE lp.book_id=target_book AND p.status='active' AND p.effective_from<=(SELECT period FROM accounting.books b WHERE b.id=target_book) AND (p.effective_to IS NULL OR p.effective_to>=(SELECT period FROM accounting.books b WHERE b.id=target_book)) AND EXISTS(SELECT 1 FROM jsonb_array_elements(p.fee_variance_reasons_json) r WHERE r->>'code'='unresolved' AND (r->>'blocks_close')::boolean=FALSE)) THEN 'blocked' ELSE 'pass' END) THEN RETURN false; END IF;
 expected_manual:=CASE WHEN EXISTS(SELECT 1 FROM accounting.manual_review_requests m WHERE m.book_id=target_book AND NOT EXISTS(SELECT 1 FROM accounting.manual_review_withdrawals w WHERE w.request_id=m.id) AND NOT EXISTS(SELECT 1 FROM accounting.vouchers v JOIN accounting.voucher_review_completions done ON done.id=v.id WHERE v.review_request_id=m.id)) THEN 'blocked' ELSE 'pass' END;
 RETURN EXISTS(SELECT 1 FROM jsonb_array_elements(checks) c WHERE c->>'code'='unposted_manual' AND c->>'status'=expected_manual);
END $$;
REVOKE ALL ON FUNCTION accounting.rr_close_checks_valid(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
