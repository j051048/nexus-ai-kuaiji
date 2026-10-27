-- compatibility: breaking
-- Preserve the full approved recurring plan/source/line contract while recognizing responsibility-driven versions.
CREATE OR REPLACE FUNCTION accounting.rr_initial_generated_preparation(target_draft TEXT,target_book TEXT,target_maker TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM (SELECT d.id AS draft_id,d.book_id,prep.maker_user_id
 FROM drafts d JOIN draft_preparations prep ON prep.draft_id=d.id AND prep.book_id=d.book_id
 WHERE d.status='draft' AND d.version=1 AND d.approved_by IS NULL AND d.approved_at IS NULL AND d.exported_at IS NULL
 AND d.last_manual_edit_version=0 AND d.preparation_claim_reason='' AND d.review_policy_version=0 AND d.review_reason=''
 AND prep.version=d.version AND prep.maker_user_id IS NOT NULL AND prep.version_transaction=accounting.rr_transaction_id() AND accounting.rr_transaction_id()<>''
 AND prep.content_revision=1+(SELECT COUNT(*) FROM draft_lines l WHERE l.draft_id=d.id AND l.book_id=d.book_id)+(SELECT COUNT(*) FROM draft_line_auxiliaries a WHERE a.draft_id=d.id AND a.book_id=d.book_id)
 AND (SELECT COUNT(*) FROM draft_preparation_history h WHERE h.draft_id=d.id)=prep.content_revision
 AND EXISTS(SELECT 1 FROM draft_preparation_history h WHERE h.draft_id=d.id AND h.content_revision=1 AND h.version=1 AND h.maker_user_id=prep.maker_user_id)
 AND EXISTS(SELECT 1 FROM draft_preparation_history h WHERE h.draft_id=d.id AND h.content_revision=prep.content_revision AND h.version=prep.version AND h.maker_user_id=prep.maker_user_id)
 AND NOT EXISTS(SELECT 1 FROM draft_preparation_history h WHERE h.draft_id=d.id AND (h.book_id<>d.book_id OR h.maker_user_id IS DISTINCT FROM prep.maker_user_id OR h.version<>1 OR h.transaction_id<>accounting.rr_transaction_id()))
 AND NOT EXISTS(SELECT 1 FROM draft_review_receipts r WHERE r.draft_id=d.id)) candidate WHERE candidate.draft_id=target_draft AND candidate.book_id=target_book AND candidate.maker_user_id=target_maker)
$$;
REVOKE ALL ON FUNCTION accounting.rr_initial_generated_preparation(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE OR REPLACE FUNCTION accounting.monthly_native_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD; original RECORD;v_plan RECORD;prior INTEGER; expected JSONB;lines JSONB;actual JSONB;BEGIN
 SELECT bk.*,lp.ledger_id,p.status INTO b FROM accounting.books bk JOIN accounting.ledger_periods lp ON lp.book_id=bk.id JOIN accounting.periods p ON p.book_id=bk.id AND p.period=bk.period WHERE bk.id=NEW.book_id AND bk.firm_id=NEW.firm_id AND bk.company_id=NEW.company_id;
 IF NOT FOUND OR b.status<>'open' THEN RAISE EXCEPTION 'Open same-company period required' USING ERRCODE='23514';END IF;
 IF TG_TABLE_NAME IN ('monthly_baselines','monthly_checks','recurring_plan_versions','recurring_plan_approvals','recurring_generations') THEN
  IF NEW.ledger_id IS DISTINCT FROM b.ledger_id THEN RAISE EXCEPTION 'Wrong long-term ledger' USING ERRCODE='23514';END IF;
 END IF;
 IF TG_TABLE_NAME='monthly_baselines' THEN
  IF NEW.original_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.original_content,'UTF8')),'hex') THEN RAISE EXCEPTION 'Original content hash mismatch' USING ERRCODE='23514';END IF;
  IF NOT accounting.monthly_lines_valid(NEW.book_id,NEW.lines_json::jsonb,true) OR NEW.version<>(SELECT coalesce(max(version),0)+1 FROM accounting.monthly_baselines WHERE book_id=NEW.book_id AND kind=NEW.kind) OR NEW.period IS DISTINCT FROM (CASE WHEN NEW.kind='opening' THEN to_char(to_date(b.period||'-01','YYYY-MM-DD')-interval '1 month','YYYY-MM') ELSE b.period END) THEN RAISE EXCEPTION 'Invalid baseline dimensions, period or version' USING ERRCODE='23514';END IF;
  IF NEW.source_kind<>'manual' AND NEW.lines_json::jsonb IS DISTINCT FROM accounting.monthly_derived_lines(NEW.book_id,NEW.kind,NEW.source_kind,NEW.source_id) THEN RAISE EXCEPTION 'Imported evidence must be preserved exactly' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='monthly_baseline_confirmations' THEN
  SELECT * INTO original FROM accounting.monthly_baselines WHERE id=NEW.baseline_id AND book_id=NEW.book_id;
  IF NOT FOUND OR original.created_by=NEW.confirmed_by OR original.version<>(SELECT max(version) FROM accounting.monthly_baselines WHERE book_id=NEW.book_id AND kind=original.kind) OR NOT accounting.monthly_lines_valid(NEW.book_id,original.lines_json::jsonb,true) THEN RAISE EXCEPTION 'Latest baseline and independent review required' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='monthly_checks' THEN
  IF NEW.inputs_json::jsonb IS DISTINCT FROM accounting.monthly_current_inputs(NEW.book_id,NEW.opening_id,NEW.closing_id) OR accounting.monthly_report_valid(NEW.book_id,NEW.opening_id,NEW.closing_id,NEW.inputs_json::jsonb,NEW.report_json::jsonb) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Monthly check must match current deterministic facts and amounts' USING ERRCODE='23514';END IF;
  IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM accounting.monthly_checks WHERE book_id=NEW.book_id) OR NOT EXISTS(SELECT 1 FROM accounting.monthly_baselines x JOIN accounting.monthly_baseline_confirmations c ON c.baseline_id=x.id WHERE x.id=NEW.opening_id AND x.book_id=NEW.book_id AND x.kind='opening' AND x.version=(SELECT max(version) FROM accounting.monthly_baselines WHERE book_id=x.book_id AND kind=x.kind)) OR NOT EXISTS(SELECT 1 FROM accounting.monthly_baselines x JOIN accounting.monthly_baseline_confirmations c ON c.baseline_id=x.id WHERE x.id=NEW.closing_id AND x.book_id=NEW.book_id AND x.kind='closing' AND x.version=(SELECT max(version) FROM accounting.monthly_baselines WHERE book_id=x.book_id AND kind=x.kind))
  OR NEW.inputs_json::jsonb->>'book_id' IS DISTINCT FROM NEW.book_id OR NEW.inputs_json::jsonb->>'period' IS DISTINCT FROM b.period OR NEW.inputs_json::jsonb->>'opening_id' IS DISTINCT FROM NEW.opening_id OR NEW.inputs_json::jsonb->>'closing_id' IS DISTINCT FROM NEW.closing_id OR NEW.report_json::jsonb->>'input_fingerprint' IS DISTINCT FROM NEW.input_fingerprint OR NEW.report_json::jsonb->'official_report' IS DISTINCT FROM 'false'::jsonb THEN RAISE EXCEPTION 'Scoped confirmed references required; never an official report' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='monthly_check_closures' THEN
  SELECT * INTO original FROM accounting.monthly_checks WHERE id=NEW.check_id AND book_id=NEW.book_id;
  IF NOT FOUND OR original.inputs_json::jsonb IS DISTINCT FROM accounting.monthly_current_inputs(NEW.book_id,original.opening_id,original.closing_id) OR EXISTS(SELECT 1 FROM accounting.monthly_baselines n WHERE n.book_id=NEW.book_id AND ((n.kind='opening' AND n.version>(SELECT version FROM accounting.monthly_baselines WHERE id=original.opening_id)) OR (n.kind='closing' AND n.version>(SELECT version FROM accounting.monthly_baselines WHERE id=original.closing_id)))) THEN RAISE EXCEPTION 'Cannot close stale monthly check' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='recurring_plan_versions' THEN
  SELECT coalesce(max(version),0)+1 INTO prior FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id;
  IF NEW.version<>prior OR EXISTS(SELECT 1 FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id AND (ledger_id<>NEW.ledger_id OR firm_id<>NEW.firm_id OR company_id<>NEW.company_id)) OR NEW.start_period !~'^[0-9]{4}-(0[1-9]|1[0-2])$' OR (NEW.end_period IS NOT NULL AND NEW.end_period !~'^[0-9]{4}-(0[1-9]|1[0-2])$') OR NOT accounting.monthly_lines_valid(NEW.book_id,NEW.lines_json::jsonb,false) OR NEW.catalog_json::jsonb IS DISTINCT FROM accounting.monthly_catalog(NEW.book_id,NEW.lines_json::jsonb) THEN RAISE EXCEPTION 'Explicit balanced amounts and current company catalog required' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME IN ('recurring_plan_approvals','recurring_generations') THEN
  SELECT * INTO v_plan FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id AND version=NEW.version AND ledger_id=NEW.ledger_id;
  IF NOT FOUND OR NEW.version<>(SELECT max(version) FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id) OR NOT accounting.monthly_lines_valid(NEW.book_id,v_plan.lines_json::jsonb,false) OR v_plan.catalog_json::jsonb IS DISTINCT FROM accounting.monthly_catalog(NEW.book_id,v_plan.lines_json::jsonb) THEN RAISE EXCEPTION 'Latest approved catalog-compatible plan required' USING ERRCODE='23514';END IF;
  IF TG_TABLE_NAME='recurring_plan_approvals' THEN
   IF v_plan.created_by=NEW.approved_by THEN RAISE EXCEPTION 'Independent plan approval required' USING ERRCODE='23514';END IF;
  ELSE
   IF NEW.period IS DISTINCT FROM b.period OR b.period<v_plan.start_period OR (v_plan.end_period IS NOT NULL AND b.period>v_plan.end_period) OR NOT EXISTS(SELECT 1 FROM accounting.recurring_plan_approvals WHERE plan_id=NEW.plan_id AND version=NEW.version)
   OR NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND d.id=NEW.draft_id AND d.status='draft' AND accounting.rr_initial_generated_preparation(d.id,d.book_id,NEW.created_by) AND NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_allowed(NEW.book_id,true) AND s.direction='expense' AND s.counterparty='' AND s.source_kind='business' AND s.external_id='monthly:'||NEW.plan_id||':'||NEW.period AND s.date=NEW.period||'-'||lpad(v_plan.day::text,2,'0') AND s.description=v_plan.description AND d.description=v_plan.description AND s.evidence_ref=v_plan.evidence_ref AND s.amount_cents=(SELECT sum((x->>'debit_cents')::bigint) FROM jsonb_array_elements(v_plan.lines_json::jsonb) x)) THEN RAISE EXCEPTION 'One unreviewed draft per plan and month required' USING ERRCODE='23514';END IF;
   SELECT coalesce(jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit_cents',l.debit_cents,'credit_cents',l.credit_cents,'auxiliary_values',coalesce(a.values_json::jsonb,'{}')) ORDER BY l.ordinal),'[]') INTO actual FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal WHERE l.draft_id=NEW.draft_id AND l.book_id=NEW.book_id;
   IF actual IS DISTINCT FROM v_plan.lines_json::jsonb THEN RAISE EXCEPTION 'Generated lines must equal approved fixed plan' USING ERRCODE='23514';END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.monthly_native_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
