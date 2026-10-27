-- compatibility: breaking
-- Accept only actual initial responsibility events, never an arbitrary pending version.
DROP VIEW IF EXISTS rr_initial_generated_preparations;
CREATE VIEW rr_initial_generated_preparations AS SELECT d.id AS draft_id,d.book_id,prep.maker_user_id
 FROM drafts d JOIN draft_preparations prep ON prep.draft_id=d.id AND prep.book_id=d.book_id
 WHERE d.status='draft' AND d.version=1 AND d.approved_by IS NULL AND d.approved_at IS NULL AND d.exported_at IS NULL
 AND d.last_manual_edit_version=0 AND d.preparation_claim_reason='' AND d.review_policy_version=0 AND d.review_reason=''
 AND prep.version=d.version AND prep.maker_user_id IS NOT NULL AND prep.version_transaction=accounting_transaction_id() AND accounting_transaction_id()<>''
 AND prep.content_revision=1+(SELECT COUNT(*) FROM draft_lines l WHERE l.draft_id=d.id AND l.book_id=d.book_id)+(SELECT COUNT(*) FROM draft_line_auxiliaries a WHERE a.draft_id=d.id AND a.book_id=d.book_id)
 AND (SELECT COUNT(*) FROM draft_preparation_history h WHERE h.draft_id=d.id)=prep.content_revision
 AND EXISTS(SELECT 1 FROM draft_preparation_history h WHERE h.draft_id=d.id AND h.content_revision=1 AND h.version=1 AND h.maker_user_id=prep.maker_user_id)
 AND EXISTS(SELECT 1 FROM draft_preparation_history h WHERE h.draft_id=d.id AND h.content_revision=prep.content_revision AND h.version=prep.version AND h.maker_user_id=prep.maker_user_id)
 AND NOT EXISTS(SELECT 1 FROM draft_preparation_history h WHERE h.draft_id=d.id AND (h.book_id<>d.book_id OR h.maker_user_id IS NOT prep.maker_user_id OR h.version<>1 OR h.transaction_id<>accounting_transaction_id()))
 AND NOT EXISTS(SELECT 1 FROM draft_review_receipts r WHERE r.draft_id=d.id);
DROP TRIGGER IF EXISTS recurring_generation_facts;
CREATE TRIGGER recurring_generation_facts BEFORE INSERT ON recurring_generations
 WHEN NOT EXISTS(SELECT 1 FROM recurring_plan_versions p JOIN recurring_plan_approvals a ON a.plan_id=p.plan_id AND a.version=p.version JOIN books b ON b.id=NEW.book_id JOIN sources s ON s.id=NEW.source_id AND s.book_id=b.id JOIN drafts d ON d.id=NEW.draft_id AND d.source_id=s.id AND d.book_id=b.id WHERE p.plan_id=NEW.plan_id AND p.version=NEW.version AND p.version=(SELECT max(version) FROM recurring_plan_versions WHERE plan_id=p.plan_id) AND p.ledger_id=NEW.ledger_id AND NEW.period=b.period AND b.period>=p.start_period AND (p.end_period IS NULL OR b.period<=p.end_period) AND d.status='draft' AND EXISTS(SELECT 1 FROM rr_initial_generated_preparations init WHERE init.draft_id=d.id AND init.book_id=d.book_id AND init.maker_user_id=NEW.created_by)
 AND NEW.created_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm()
 AND EXISTS(SELECT 1 FROM service_book_user_permissions permission WHERE permission.book_id=NEW.book_id AND permission.user_id=accounting_actor_user() AND permission.can_edit)
 AND s.direction='expense' AND s.counterparty='' AND s.source_kind='business' AND s.external_id='monthly:'||p.plan_id||':'||NEW.period AND s.description=p.description AND d.description=p.description AND s.evidence_ref=p.evidence_ref AND s.date=NEW.period||'-'||printf('%02d',p.day) AND s.amount_cents=(SELECT sum(json_extract(value,'$.debit_cents')) FROM json_each(p.lines_json)))
 OR (SELECT count(*) FROM draft_lines WHERE draft_id=NEW.draft_id AND book_id=NEW.book_id)<>(SELECT json_array_length(lines_json) FROM recurring_plan_versions WHERE plan_id=NEW.plan_id AND version=NEW.version)
 OR EXISTS(SELECT 1 FROM recurring_plan_versions p,json_each(p.lines_json) x WHERE p.plan_id=NEW.plan_id AND p.version=NEW.version AND NOT EXISTS(SELECT 1 FROM draft_lines l LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal WHERE l.draft_id=NEW.draft_id AND l.book_id=NEW.book_id AND l.ordinal=x.key AND l.account_code=json_extract(x.value,'$.account_code') AND l.debit_cents=json_extract(x.value,'$.debit_cents') AND l.credit_cents=json_extract(x.value,'$.credit_cents') AND (SELECT count(*) FROM json_each(coalesce(a.values_json,'{}')))=(SELECT count(*) FROM json_each(json_extract(x.value,'$.auxiliary_values'))) AND NOT EXISTS(SELECT 1 FROM json_each(json_extract(x.value,'$.auxiliary_values')) kv WHERE json_extract(coalesce(a.values_json,'{}'),'$.'||kv.key) IS NOT kv.value)))
 BEGIN SELECT RAISE(ABORT,'Exact unreviewed generated draft required');END;
