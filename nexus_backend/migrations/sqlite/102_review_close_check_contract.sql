-- compatibility: breaking
-- Preserve the six original close checks and require the truthful seventh manual-workflow check.
DROP TRIGGER p0_close_insert_checks_guard;
CREATE TRIGGER p0_close_insert_checks_guard BEFORE INSERT ON internal_close_runs
WHEN NOT json_valid(NEW.checks_json) OR json_type(NEW.checks_json)<>'array' OR json_array_length(NEW.checks_json)<>7
 OR (SELECT count(DISTINCT json_extract(c.value,'$.code')) FROM json_each(NEW.checks_json) c
  WHERE json_extract(c.value,'$.code') IN('unposted_periodic','unapproved_drafts','subledger_control',
   'bank_reconciliation','trial_balance','report_checks','unposted_manual'))<>7
 OR EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE coalesce(json_extract(c.value,'$.status'),'') NOT IN('pass','blocked'))
 OR (NEW.status IN('preflight_passed','reviewed','locked') AND EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE json_extract(c.value,'$.status')<>'pass'))
 OR (NEW.status='preflight_failed' AND NOT EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE json_extract(c.value,'$.status')='blocked'))
 OR NOT EXISTS(SELECT 1 FROM json_each(NEW.checks_json) j WHERE json_extract(j.value,'$.code')='unposted_manual' AND json_extract(j.value,'$.status')=CASE WHEN EXISTS(SELECT 1 FROM manual_review_requests m WHERE m.book_id=NEW.book_id AND NOT EXISTS(SELECT 1 FROM manual_review_withdrawals w WHERE w.request_id=m.id) AND NOT EXISTS(SELECT 1 FROM vouchers v JOIN voucher_review_completions c ON c.id=v.id WHERE v.review_request_id=m.id)) THEN 'blocked' ELSE 'pass' END)
BEGIN SELECT RAISE(ABORT,'Close preflight checks must be complete and consistent'); END;
CREATE TRIGGER rr_close_checks_transition BEFORE UPDATE ON internal_close_runs WHEN NEW.status IN('reviewed','locked') AND OLD.status<>NEW.status AND (NOT json_valid(NEW.checks_json) OR json_type(NEW.checks_json)<>'array' OR json_array_length(NEW.checks_json)<>7
 OR (SELECT count(DISTINCT json_extract(c.value,'$.code')) FROM json_each(NEW.checks_json) c
  WHERE json_extract(c.value,'$.code') IN('unposted_periodic','unapproved_drafts','subledger_control',
   'bank_reconciliation','trial_balance','report_checks','unposted_manual'))<>7
 OR EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE coalesce(json_extract(c.value,'$.status'),'') NOT IN('pass','blocked'))
 OR (NEW.status IN('preflight_passed','reviewed','locked') AND EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE json_extract(c.value,'$.status')<>'pass'))
 OR (NEW.status='preflight_failed' AND NOT EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE json_extract(c.value,'$.status')='blocked'))
 OR NOT EXISTS(SELECT 1 FROM json_each(NEW.checks_json) j WHERE json_extract(j.value,'$.code')='unposted_manual' AND json_extract(j.value,'$.status')=CASE WHEN EXISTS(SELECT 1 FROM manual_review_requests m WHERE m.book_id=NEW.book_id AND NOT EXISTS(SELECT 1 FROM manual_review_withdrawals w WHERE w.request_id=m.id) AND NOT EXISTS(SELECT 1 FROM vouchers v JOIN voucher_review_completions c ON c.id=v.id WHERE v.review_request_id=m.id)) THEN 'blocked' ELSE 'pass' END)) BEGIN SELECT RAISE(ABORT,'Close preflight checks must be complete and consistent'); END;
CREATE TRIGGER rr_close_lock_checks BEFORE INSERT ON internal_period_locks WHEN NOT EXISTS(SELECT 1 FROM internal_close_runs r WHERE r.id=NEW.close_run_id AND r.book_id=NEW.book_id AND NOT(NOT json_valid(r.checks_json) OR json_type(r.checks_json)<>'array' OR json_array_length(r.checks_json)<>7
 OR (SELECT count(DISTINCT json_extract(c.value,'$.code')) FROM json_each(r.checks_json) c
  WHERE json_extract(c.value,'$.code') IN('unposted_periodic','unapproved_drafts','subledger_control',
   'bank_reconciliation','trial_balance','report_checks','unposted_manual'))<>7
 OR EXISTS(SELECT 1 FROM json_each(r.checks_json) c WHERE coalesce(json_extract(c.value,'$.status'),'') NOT IN('pass','blocked'))
 OR (r.status IN('preflight_passed','reviewed','locked') AND EXISTS(SELECT 1 FROM json_each(r.checks_json) c WHERE json_extract(c.value,'$.status')<>'pass'))
 OR (r.status='preflight_failed' AND NOT EXISTS(SELECT 1 FROM json_each(r.checks_json) c WHERE json_extract(c.value,'$.status')='blocked'))
 OR NOT EXISTS(SELECT 1 FROM json_each(r.checks_json) j WHERE json_extract(j.value,'$.code')='unposted_manual' AND json_extract(j.value,'$.status')=CASE WHEN EXISTS(SELECT 1 FROM manual_review_requests m WHERE m.book_id=r.book_id AND NOT EXISTS(SELECT 1 FROM manual_review_withdrawals w WHERE w.request_id=m.id) AND NOT EXISTS(SELECT 1 FROM vouchers v JOIN voucher_review_completions c ON c.id=v.id WHERE v.review_request_id=m.id)) THEN 'blocked' ELSE 'pass' END))) BEGIN SELECT RAISE(ABORT,'Close preflight checks must be complete and consistent'); END;
