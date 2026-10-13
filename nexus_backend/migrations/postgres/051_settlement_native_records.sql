-- Forward-only fix: PL/pgSQL record variables use v_ prefixes so SQL aliases
-- cannot resolve to an unassigned procedural record. Previous receipts remain intact.

CREATE OR REPLACE FUNCTION accounting.settlement_clearing_account(target_book TEXT,target_source TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_source RECORD; c TEXT; n INTEGER; total BIGINT; BEGIN
 SELECT * INTO v_source FROM accounting.sources WHERE id=target_source AND book_id=target_book AND source_kind='business';IF NOT FOUND THEN RETURN NULL;END IF;
 SELECT min(account_code),count(*),sum(amount)::bigint INTO c,n,total FROM (
  SELECT l.account_code,sum(CASE WHEN v_source.direction='expense' THEN credit_cents ELSE debit_cents END) AS amount FROM accounting.draft_lines l JOIN accounting.drafts d ON d.id=l.draft_id AND d.book_id=l.book_id WHERE d.source_id=target_source AND d.book_id=target_book
  AND ((v_source.direction='expense' AND l.account_code IN ('2202','2241')) OR (v_source.direction='income' AND l.account_code IN ('1122','1221'))) GROUP BY l.account_code HAVING sum(CASE WHEN v_source.direction='expense' THEN credit_cents ELSE debit_cents END)>0
 ) codes;
 IF n<>1 OR total<>v_source.amount_cents THEN RETURN NULL; END IF;RETURN c;END $$;

CREATE OR REPLACE FUNCTION accounting.settlement_open_item(target_book TEXT,target_source TEXT,target_party TEXT,target_version INTEGER,target_key TEXT,target_fp TEXT,target_reason TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_source RECORD; v_prior RECORD; code TEXT; result TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
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

CREATE OR REPLACE FUNCTION accounting.settlement_build_plan(target_book TEXT,body JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_bank RECORD; v_item RECORD; part JSONB; amount BIGINT; total BIGINT:=0; remaining BIGINT; party TEXT; pieces JSONB:='[]'; BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Settlement reviewer scope required' USING ERRCODE='42501';END IF;
 IF jsonb_typeof(body) IS DISTINCT FROM 'object' OR jsonb_typeof(body->'parts') IS DISTINCT FROM 'array' OR jsonb_array_length(body->'parts') NOT BETWEEN 1 AND 50 OR jsonb_typeof(body->'bank_source_id') IS DISTINCT FROM 'string' OR jsonb_typeof(body->'reason') IS DISTINCT FROM 'string' OR length(trim(COALESCE(body->>'reason',''))) NOT BETWEEN 3 AND 1000 OR EXISTS(SELECT 1 FROM jsonb_object_keys(body) k WHERE k NOT IN ('bank_source_id','parts','reason')) THEN RAISE EXCEPTION 'Invalid bounded allocation';END IF;
 SELECT s.*,b.company_id,p.status AS period_status,d.version AS draft_version,d.status AS draft_status INTO v_bank FROM accounting.sources s JOIN accounting.books b ON b.id=s.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=body->>'bank_source_id' AND s.book_id=target_book AND s.source_kind='bank';
 IF NOT FOUND OR v_bank.period_status<>'open' OR v_bank.draft_status<>'draft' OR accounting.source_inactive(target_book,v_bank.id) OR accounting.bank_source_import_blocked(target_book,v_bank.id) OR accounting.case_source_blocked(target_book,v_bank.id) OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE bank_source_id=v_bank.id) THEN RAISE EXCEPTION 'Current open unreviewed bank source required';END IF;
 SELECT entity_id INTO party FROM accounting.master_resolve(v_bank.company_id,'counterparty',v_bank.counterparty);IF party IS NULL THEN RAISE EXCEPTION 'Approved bank party mapping required';END IF;
 remaining:=v_bank.amount_cents-accounting.settlement_used(NULL,v_bank.id);
 IF (SELECT count(DISTINCT value->>'item_id') FROM jsonb_array_elements(body->'parts'))<>jsonb_array_length(body->'parts') THEN RAISE EXCEPTION 'Distinct items required';END IF;
 FOR part IN SELECT value FROM jsonb_array_elements(body->'parts') ORDER BY value->>'item_id' LOOP
  IF jsonb_typeof(part) IS DISTINCT FROM 'object' OR jsonb_typeof(part->'item_id') IS DISTINCT FROM 'string' OR jsonb_typeof(part->'amount') IS DISTINCT FROM 'string' OR EXISTS(SELECT 1 FROM jsonb_object_keys(part) k WHERE k NOT IN ('item_id','amount')) OR COALESCE(part->>'amount','') !~ '^(0|[1-9][0-9]{0,11})\.[0-9]{2}$' THEN RAISE EXCEPTION 'Exact cents required';END IF;
  amount:=accounting.learning_money_cents(part->>'amount');
  SELECT i.*,d.version AS draft_version,d.status AS draft_status INTO v_item FROM accounting.settlement_items i JOIN accounting.drafts d ON d.source_id=i.source_id AND d.book_id=i.book_id WHERE i.id=part->>'item_id' AND i.book_id=target_book AND i.firm_id=accounting.actor_firm();
  IF NOT FOUND OR NOT accounting.settlement_item_current(v_item.id) OR v_item.direction<>v_bank.direction OR v_item.counterparty_id<>party OR amount<=0 OR amount>v_item.amount_cents-accounting.settlement_used(v_item.id,NULL) OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE event_id=v_item.event_id) THEN RAISE EXCEPTION 'Scoped current item, party, direction and remaining balance required';END IF;
  pieces:=pieces||jsonb_build_array(jsonb_build_object('item_id',v_item.id,'amount_cents',amount,'remaining_before',v_item.amount_cents-accounting.settlement_used(v_item.id,NULL),'fact_version',v_item.fact_version,'clearing_account',v_item.clearing_account,'business_draft_version',v_item.draft_version,'business_draft_status',v_item.draft_status,'source_fingerprint',v_item.source_fingerprint));total:=total+amount;
 END LOOP;
 IF total>remaining THEN RAISE EXCEPTION 'Allocation exceeds bank remaining balance';END IF;
 RETURN jsonb_build_object('book_id',target_book,'bank_source_id',v_bank.id,'bank_fingerprint',v_bank.fingerprint,'bank_draft_version',v_bank.draft_version,'bank_remaining_before',remaining,'counterparty_id',party,'parts',pieces,'total_cents',total,'reason',body->>'reason');END $$;

CREATE OR REPLACE FUNCTION accounting.settlement_make_preview(target_book TEXT,body TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE result TEXT; plan JSONB; v_book RECORD; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);plan:=accounting.settlement_build_plan(target_book,body::jsonb);SELECT * INTO v_book FROM accounting.books WHERE id=target_book;
 result:=md5(random()::text||clock_timestamp()::text||target_book);INSERT INTO accounting.settlement_previews VALUES(result,target_book,v_book.firm_id,v_book.company_id,accounting.actor_user(),body,plan::text,clock_timestamp()::text,(clock_timestamp()+interval '10 minutes')::text);RETURN result;END $$;

CREATE OR REPLACE FUNCTION accounting.settlement_confirm(target_book TEXT,target_preview TEXT,target_key TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_preview RECORD; v_prior RECORD; body JSONB; part JSONB; result TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current settlement reviewer required' USING ERRCODE='42501';END IF;
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
 PERFORM pg_advisory_xact_lock(6420031786281903101);IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current settlement reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_allocation FROM accounting.settlement_allocations WHERE id=target AND book_id=target_book AND firm_id=accounting.actor_firm();IF NOT FOUND OR v_allocation.created_by=accounting.actor_user() THEN RAISE EXCEPTION 'Independent scoped reversal reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_prior FROM accounting.settlement_reversals WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN IF v_prior.allocation_id<>target OR v_prior.expected_fingerprint<>target_fp OR v_prior.reason<>target_reason THEN RAISE EXCEPTION 'Reversal key conflict';END IF;RETURN v_prior.id;END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period JOIN accounting.drafts d ON d.book_id=b.id AND d.source_id=v_allocation.bank_source_id WHERE b.id=target_book AND p.status='open' AND d.status='draft') OR target_fp IS DISTINCT FROM accounting.settlement_allocation_fingerprint(target) OR length(target_key) NOT BETWEEN 8 AND 120 OR length(trim(target_reason)) NOT BETWEEN 3 AND 1000 THEN RAISE EXCEPTION 'Frozen bank evidence needs adjustment; stale reversal forbidden';END IF;
 result:=md5(random()::text||clock_timestamp()::text);INSERT INTO accounting.settlement_reversals VALUES(result,target,target_book,v_allocation.firm_id,v_allocation.company_id,accounting.actor_user(),clock_timestamp()::text,target_key,target_reason,target_fp);RETURN result;END $$;

CREATE OR REPLACE FUNCTION accounting.settlement_bank_clearance_valid(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_bank RECORD; expected JSONB; actual JSONB; BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RETURN false;END IF;
 SELECT * INTO v_bank FROM accounting.sources WHERE id=target_source AND book_id=target_book;IF NOT FOUND OR v_bank.source_kind<>'bank' THEN RETURN true;END IF;
 IF accounting.settlement_used(NULL,target_source)=0 THEN RETURN true;END IF;
 IF accounting.settlement_used(NULL,target_source)<>v_bank.amount_cents OR EXISTS(
  SELECT 1 FROM accounting.settlement_allocations a JOIN accounting.settlement_items i ON i.id=a.item_id JOIN accounting.drafts d ON d.source_id=i.source_id AND d.book_id=i.book_id WHERE a.bank_source_id=target_source AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id) AND (d.status NOT IN ('approved','exported') OR NOT accounting.settlement_item_current(i.id) OR i.clearing_account<>a.clearing_account OR NOT EXISTS(SELECT 1 FROM accounting.master_resolve(i.company_id,'counterparty',v_bank.counterparty) q WHERE q.entity_id=i.counterparty_id))) THEN RETURN false;END IF;
 SELECT jsonb_object_agg(code,jsonb_build_array(debit,credit)) INTO expected FROM (
  SELECT a.clearing_account AS code,CASE WHEN v_bank.direction='expense' THEN sum(a.amount_cents) ELSE 0 END AS debit,CASE WHEN v_bank.direction='income' THEN sum(a.amount_cents) ELSE 0 END AS credit FROM accounting.settlement_allocations a WHERE a.bank_source_id=target_source AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id) GROUP BY a.clearing_account
  UNION ALL SELECT '1002',CASE WHEN v_bank.direction='income' THEN v_bank.amount_cents ELSE 0 END,CASE WHEN v_bank.direction='expense' THEN v_bank.amount_cents ELSE 0 END) expected_rows;
 SELECT jsonb_object_agg(code,jsonb_build_array(debit,credit)) INTO actual FROM (SELECT l.account_code AS code,sum(l.debit_cents) AS debit,sum(l.credit_cents) AS credit FROM accounting.draft_lines l JOIN accounting.drafts d ON d.id=l.draft_id AND d.book_id=l.book_id WHERE d.source_id=target_source AND d.book_id=target_book GROUP BY l.account_code) lines;
 RETURN actual IS NOT DISTINCT FROM expected;END $$;
