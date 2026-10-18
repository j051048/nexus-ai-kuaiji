-- compatibility: incremental
-- Security-definer helpers invoked from a company transaction must not fall
-- back to the legacy global lock. B04 closing functions and the ops-dirty
-- helper set/lock the resolved company explicitly after validating the book.

CREATE OR REPLACE FUNCTION accounting.mark_ops_company_dirty(target_firm text, target_company text)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'accounting', 'pg_temp'
AS $function$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company) THEN RETURN; END IF;
 IF coalesce(current_setting('app.write_scope_company',true),'')='' THEN
  PERFORM set_config('app.write_scope_firm',target_firm,true);
  PERFORM set_config('app.write_scope_company',target_company,true);
 END IF;
 INSERT INTO accounting.ops_company_dirty(firm_id,company_id,dirty_at) VALUES(target_firm,target_company,clock_timestamp()::text)
 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO accounting.ops_company_refresh(firm_id,company_id,next_due_at) VALUES(target_firm,target_company,clock_timestamp()::text) ON CONFLICT DO NOTHING;
 END $function$;

CREATE OR REPLACE FUNCTION accounting.review_closing_snapshot(target_book text, target_snapshot text, target_reason text, confirmed boolean)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'accounting', 'pg_temp'
AS $function$
 DECLARE item RECORD; book RECORD; BEGIN
 SELECT * INTO book FROM accounting.books WHERE id=target_book AND firm_id=accounting.actor_firm();
 IF NOT FOUND THEN RAISE EXCEPTION 'book scope denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock_shared(6420031786281903101);
 PERFORM pg_advisory_xact_lock(accounting.company_lock_key(book.firm_id,book.company_id));
 IF NOT accounting.ai_book_reviewer(target_book) OR confirmed IS DISTINCT FROM true OR target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 3 AND 1000
 THEN RAISE EXCEPTION 'current reviewer and explicit material coverage confirmation required' USING ERRCODE='42501'; END IF;
 SELECT * INTO item FROM accounting.closing_snapshots WHERE id=target_snapshot AND book_id=target_book AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=item.book_id AND period=item.period AND status='open')
 OR item.input_fingerprint IS DISTINCT FROM accounting.closing_fingerprint(target_book)
 OR item.report_json::jsonb IS DISTINCT FROM accounting.closing_report(target_book) OR (item.report_json::jsonb->>'passed')::BOOLEAN IS DISTINCT FROM true
 THEN RAISE EXCEPTION 'preflight is blocked or stale; recompute before review' USING ERRCODE='23514'; END IF;
 INSERT INTO accounting.closing_reviews(snapshot_id,book_id,reviewed_by,reason,materials_confirmed,created_at)
 VALUES(target_snapshot,target_book,accounting.actor_user(),target_reason,true,accounting.ocr_utc_now()) ON CONFLICT DO NOTHING;
 END $function$;

CREATE OR REPLACE FUNCTION accounting.close_with_snapshot(target_book text, target_snapshot text, target_reason text)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'accounting', 'pg_temp'
AS $function$
 DECLARE item RECORD; prior RECORD; book RECORD; stamp TEXT; BEGIN
 SELECT * INTO book FROM accounting.books WHERE id=target_book AND firm_id=accounting.actor_firm();
 IF NOT FOUND THEN RAISE EXCEPTION 'book scope denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock_shared(6420031786281903101);
 PERFORM pg_advisory_xact_lock(accounting.company_lock_key(book.firm_id,book.company_id));
 IF NOT accounting.ai_book_reviewer(target_book) OR target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 3 AND 1000 THEN
 RAISE EXCEPTION 'current reviewer and close reason required' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM accounting.closing_receipts WHERE book_id=target_book;
 IF FOUND THEN IF prior.snapshot_id<>target_snapshot OR prior.closed_by<>accounting.actor_user() OR prior.reason<>target_reason
 THEN RAISE EXCEPTION 'period already closed with a different reviewed snapshot'; END IF; RETURN; END IF;
 SELECT * INTO item FROM accounting.closing_snapshots WHERE id=target_snapshot AND book_id=target_book AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR item.input_fingerprint IS DISTINCT FROM accounting.closing_fingerprint(target_book)
 OR item.report_json::jsonb IS DISTINCT FROM accounting.closing_report(target_book) OR (item.report_json::jsonb->>'passed')::BOOLEAN IS DISTINCT FROM true
 OR NOT EXISTS(SELECT 1 FROM accounting.closing_reviews WHERE snapshot_id=item.id AND book_id=target_book AND reviewed_by=accounting.actor_user() AND materials_confirmed)
 OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=item.book_id AND period=item.period AND status='open')
 THEN RAISE EXCEPTION 'current passed and explicitly reviewed snapshot required' USING ERRCODE='23514'; END IF;
 stamp:=accounting.ocr_utc_now();
 INSERT INTO accounting.closing_receipts(book_id,period,snapshot_id,closed_by,reason,created_at) VALUES(target_book,item.period,item.id,accounting.actor_user(),target_reason,stamp);
 UPDATE accounting.periods SET status='closed' WHERE book_id=target_book AND period=item.period AND status='open';
 INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
 VALUES(item.firm_id,target_book,accounting.actor_user(),'period.close',item.period,jsonb_build_object('snapshot_id',item.id,'input_fingerprint',item.input_fingerprint,'reason',target_reason)::text,stamp);
 END $function$;
