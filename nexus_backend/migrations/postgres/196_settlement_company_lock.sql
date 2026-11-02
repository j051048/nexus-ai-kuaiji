-- compatibility: incremental
-- Match the existing company-scoped settlement service transactions. Only the
-- four native mutation entry locks change from 051/077; business guards, OIDs,
-- owner, SECURITY DEFINER search_path and existing EXECUTE ACLs are preserved.
-- lock_company_write keeps the exclusive legacy gate for callers without a
-- declared scope; scoped calls take the shared compatibility gate, then the
-- stable company lock. A different declared company is rejected, never widened.
-- CREATE OR REPLACE is replay-safe. No historical migration/checksum is edited.

CREATE OR REPLACE FUNCTION accounting.settlement_open_item(target_book TEXT,target_source TEXT,target_party TEXT,target_version INTEGER,target_key TEXT,target_fp TEXT,target_reason TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_source RECORD; v_prior RECORD; code TEXT; result TEXT; BEGIN
 PERFORM accounting.lock_company_write(
  (SELECT firm_id FROM accounting.books WHERE id=target_book),
  (SELECT company_id FROM accounting.books WHERE id=target_book));
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Settlement reviewer scope required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_prior FROM accounting.settlement_items WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN IF v_prior.book_id<>target_book OR v_prior.source_id<>target_source OR v_prior.counterparty_id<>target_party OR v_prior.request_fingerprint<>target_fp THEN RAISE EXCEPTION 'Open item idempotency conflict';END IF;RETURN v_prior.id;END IF;
 SELECT src.*,b.firm_id,b.company_id,p.status AS period_status,d.version AS draft_version,d.status AS draft_status,m.event_id,e.fact_version,e.status AS event_status,e.payload::jsonb AS facts INTO v_source FROM accounting.sources src JOIN accounting.books b ON b.id=src.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period JOIN accounting.drafts d ON d.source_id=src.id AND d.book_id=src.book_id JOIN accounting.business_mappings m ON m.source_id=src.id AND m.book_id=src.book_id JOIN accounting.business_events e ON e.id=m.event_id WHERE src.id=target_source AND src.book_id=target_book AND src.source_kind='business';
 code:=accounting.settlement_clearing_account(target_book,target_source);
 IF NOT FOUND OR v_source.period_status<>'open' OR v_source.draft_status<>'draft' OR v_source.draft_version<>target_version OR v_source.event_status<>'handed_off' OR v_source.facts->>'payment_status' IS DISTINCT FROM 'unpaid' OR code IS NULL OR v_source.amount_cents<>accounting.learning_money_cents(v_source.facts->>'amount') OR v_source.direction IS DISTINCT FROM v_source.facts->>'direction' OR v_source.counterparty IS DISTINCT FROM v_source.facts->>'counterparty' OR v_source.date IS DISTINCT FROM v_source.facts->>'occurred_on' OR accounting.source_inactive(target_book,target_source)
 OR accounting.revision_source_blocked(target_book,target_source) OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE event_id=v_source.event_id) OR NOT EXISTS(SELECT 1 FROM accounting.master_resolve(v_source.company_id,'counterparty',v_source.counterparty) q WHERE q.entity_id=target_party)
 OR length(target_key) NOT BETWEEN 8 AND 120 OR length(target_fp)<>64 OR length(trim(target_reason)) NOT BETWEEN 3 AND 1000 THEN RAISE EXCEPTION 'Current unpaid original and approved party required';END IF;
 result:=md5(random()::text||clock_timestamp()::text||target_source);
 INSERT INTO accounting.settlement_items(id,firm_id,company_id,book_id,source_id,event_id,fact_version,source_fingerprint,counterparty_id,counterparty_original,amount_cents,direction,clearing_account,created_by,created_at,idempotency_key,request_fingerprint,reason) VALUES(result,v_source.firm_id,v_source.company_id,target_book,v_source.id,v_source.event_id,v_source.fact_version,v_source.fingerprint,target_party,v_source.counterparty,v_source.amount_cents,v_source.direction,code,accounting.actor_user(),clock_timestamp()::text,target_key,target_fp,target_reason);
 RETURN result;END $$;

CREATE OR REPLACE FUNCTION accounting.settlement_make_preview(target_book TEXT,body TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE result TEXT; plan JSONB; v_book RECORD; BEGIN
 PERFORM accounting.lock_company_write(
  (SELECT firm_id FROM accounting.books WHERE id=target_book),
  (SELECT company_id FROM accounting.books WHERE id=target_book));plan:=accounting.settlement_build_plan(target_book,body::jsonb);SELECT * INTO v_book FROM accounting.books WHERE id=target_book;
 result:=md5(random()::text||clock_timestamp()::text||target_book);INSERT INTO accounting.settlement_previews VALUES(result,target_book,v_book.firm_id,v_book.company_id,accounting.actor_user(),body,plan::text,clock_timestamp()::text,(clock_timestamp()+interval '10 minutes')::text);RETURN result;END $$;

CREATE OR REPLACE FUNCTION accounting.settlement_confirm(target_book TEXT,target_preview TEXT,target_key TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_preview RECORD; v_prior RECORD; body JSONB; part JSONB; result TEXT; BEGIN
 PERFORM accounting.lock_company_write(
  (SELECT firm_id FROM accounting.books WHERE id=target_book),
  (SELECT company_id FROM accounting.books WHERE id=target_book));IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current settlement reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_preview FROM accounting.settlement_previews WHERE id=target_preview AND book_id=target_book AND firm_id=accounting.actor_firm() AND created_by=accounting.actor_user();IF NOT FOUND THEN RAISE EXCEPTION 'Private preview scope required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_prior FROM accounting.settlement_commands WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN IF v_prior.preview_id<>target_preview OR v_prior.book_id<>target_book THEN RAISE EXCEPTION 'Allocation key conflict';END IF;RETURN v_prior.id;END IF;
 body:=accounting.settlement_build_plan(target_book,v_preview.request_json::jsonb);
 IF v_preview.expires_at::timestamptz<clock_timestamp() OR body IS DISTINCT FROM v_preview.plan_json::jsonb OR length(target_key) NOT BETWEEN 8 AND 120 THEN RAISE EXCEPTION 'Allocation preview expired or stale';END IF;
 result:=md5(random()::text||clock_timestamp()::text||target_preview);INSERT INTO accounting.settlement_commands VALUES(result,target_book,v_preview.firm_id,v_preview.company_id,target_preview,accounting.actor_user(),target_key,clock_timestamp()::text);
 FOR part IN SELECT value FROM jsonb_array_elements(body->'parts') LOOP
 INSERT INTO accounting.settlement_allocations VALUES(md5(random()::text||clock_timestamp()::text),result,target_book,v_preview.firm_id,v_preview.company_id,part->>'item_id',body->>'bank_source_id',(part->>'amount_cents')::bigint,part->>'clearing_account',accounting.actor_user(),clock_timestamp()::text);END LOOP;RETURN result;END $$;

CREATE OR REPLACE FUNCTION accounting.settlement_reverse(target_book TEXT,target TEXT,target_fp TEXT,target_key TEXT,target_reason TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_allocation RECORD; v_prior RECORD; result TEXT; BEGIN
 PERFORM accounting.lock_company_write(
  (SELECT firm_id FROM accounting.books WHERE id=target_book),
  (SELECT company_id FROM accounting.books WHERE id=target_book));IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current settlement reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_allocation FROM accounting.settlement_allocations WHERE id=target AND book_id=target_book AND firm_id=accounting.actor_firm();IF NOT FOUND OR v_allocation.created_by=accounting.actor_user() THEN RAISE EXCEPTION 'Independent scoped reversal reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_prior FROM accounting.settlement_reversals WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN IF v_prior.allocation_id<>target OR v_prior.expected_fingerprint<>target_fp OR v_prior.reason<>target_reason THEN RAISE EXCEPTION 'Reversal key conflict';END IF;RETURN v_prior.id;END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period JOIN accounting.drafts d ON d.book_id=b.id AND d.source_id=v_allocation.bank_source_id WHERE b.id=target_book AND p.status='open' AND d.status='draft') OR target_fp IS DISTINCT FROM accounting.settlement_allocation_fingerprint(target) OR length(target_key) NOT BETWEEN 8 AND 120 OR length(trim(target_reason)) NOT BETWEEN 3 AND 1000 THEN RAISE EXCEPTION 'Frozen bank evidence needs adjustment; stale reversal forbidden';END IF;
 result:=md5(random()::text||clock_timestamp()::text);INSERT INTO accounting.settlement_reversals VALUES(result,target,target_book,v_allocation.firm_id,v_allocation.company_id,accounting.actor_user(),clock_timestamp()::text,target_key,target_reason,target_fp);RETURN result;END $$;
