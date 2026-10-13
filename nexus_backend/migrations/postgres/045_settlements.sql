-- Auxiliary open items, never mutable source facts or a formal subledger.
CREATE TABLE accounting.settlement_items(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,source_id TEXT NOT NULL UNIQUE,event_id TEXT NOT NULL,
 fact_version INTEGER NOT NULL,source_fingerprint TEXT NOT NULL,counterparty_id TEXT NOT NULL,counterparty_kind TEXT NOT NULL DEFAULT 'counterparty' CHECK(counterparty_kind='counterparty'),counterparty_original TEXT NOT NULL,
 amount_cents BIGINT NOT NULL CHECK(amount_cents>0),direction TEXT NOT NULL CHECK(direction IN ('expense','income')),clearing_account TEXT NOT NULL CHECK(clearing_account IN ('2202','2241','1122','1221')),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,reason TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(counterparty_id,firm_id,company_id,counterparty_kind) REFERENCES accounting.company_master_entities(id,firm_id,company_id,kind)
);
CREATE TABLE accounting.settlement_previews(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),
 request_json TEXT NOT NULL,plan_json TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.settlement_commands(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,preview_id TEXT NOT NULL UNIQUE,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),idempotency_key TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES accounting.settlement_previews(id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.settlement_allocations(
 id TEXT PRIMARY KEY,command_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,item_id TEXT NOT NULL,
 bank_source_id TEXT NOT NULL,amount_cents BIGINT NOT NULL CHECK(amount_cents>0),clearing_account TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,UNIQUE(id,book_id,firm_id,company_id),
 FOREIGN KEY(command_id,book_id,firm_id,company_id) REFERENCES accounting.settlement_commands(id,book_id,firm_id,company_id),
 FOREIGN KEY(item_id,book_id,firm_id,company_id) REFERENCES accounting.settlement_items(id,book_id,firm_id,company_id),FOREIGN KEY(bank_source_id,book_id) REFERENCES accounting.sources(id,book_id)
);
CREATE TABLE accounting.settlement_reversals(
 id TEXT PRIMARY KEY,allocation_id TEXT NOT NULL UNIQUE,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,idempotency_key TEXT NOT NULL,reason TEXT NOT NULL,expected_fingerprint TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),FOREIGN KEY(allocation_id,book_id,firm_id,company_id) REFERENCES accounting.settlement_allocations(id,book_id,firm_id,company_id)
);
CREATE INDEX idx_settlement_item_page ON accounting.settlement_items(book_id,created_at,id);
CREATE INDEX idx_settlement_bank_balance ON accounting.settlement_allocations(bank_source_id,item_id);
CREATE INDEX idx_settlement_item_balance ON accounting.settlement_allocations(item_id,bank_source_id);
DO $$ DECLARE tab TEXT; BEGIN
 FOREACH tab IN ARRAY ARRAY['settlement_items','settlement_previews','settlement_commands','settlement_allocations','settlement_reversals'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',tab);EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',tab);EXECUTE format('GRANT SELECT ON accounting.%I TO accounting_app',tab);
  EXECUTE format('CREATE POLICY settlement_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.ai_book_reviewer(book_id))',tab);
  EXECUTE format('CREATE TRIGGER settlement_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',tab);
  EXECUTE format('CREATE TRIGGER settlement_dirty AFTER INSERT ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ops_mark_changed()',tab);
 END LOOP;
END $$;
CREATE FUNCTION accounting.settlement_used(target_item TEXT,target_bank TEXT) RETURNS BIGINT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT COALESCE(sum(a.amount_cents),0)::bigint FROM accounting.settlement_allocations a WHERE (target_item IS NOT NULL AND a.item_id=target_item OR target_bank IS NOT NULL AND a.bank_source_id=target_bank) AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id)
$$;
CREATE FUNCTION accounting.settlement_clearing_account(target_book TEXT,target_source TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE s RECORD; c TEXT; n INTEGER; total BIGINT; BEGIN
 SELECT * INTO s FROM accounting.sources WHERE id=target_source AND book_id=target_book AND source_kind='business';IF NOT FOUND THEN RETURN NULL;END IF;
 SELECT min(account_code),count(*),sum(amount)::bigint INTO c,n,total FROM (
  SELECT l.account_code,sum(CASE WHEN s.direction='expense' THEN credit_cents ELSE debit_cents END) AS amount FROM accounting.draft_lines l JOIN accounting.drafts d ON d.id=l.draft_id AND d.book_id=l.book_id WHERE d.source_id=target_source AND d.book_id=target_book
  AND ((s.direction='expense' AND l.account_code IN ('2202','2241')) OR (s.direction='income' AND l.account_code IN ('1122','1221'))) GROUP BY l.account_code HAVING sum(CASE WHEN s.direction='expense' THEN credit_cents ELSE debit_cents END)>0
 ) codes;
 IF n<>1 OR total<>s.amount_cents THEN RETURN NULL; END IF;RETURN c;END $$;
CREATE FUNCTION accounting.settlement_item_current(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.settlement_items i JOIN accounting.sources s ON s.id=i.source_id AND s.book_id=i.book_id JOIN accounting.business_mappings m ON m.source_id=i.source_id AND m.book_id=i.book_id JOIN accounting.business_events e ON e.id=m.event_id
 WHERE i.id=target AND e.status='handed_off' AND e.fact_version=i.fact_version AND s.fingerprint=i.source_fingerprint AND e.payload::jsonb->>'payment_status'='unpaid' AND s.direction=e.payload::jsonb->>'direction' AND s.date=e.payload::jsonb->>'occurred_on' AND s.counterparty=e.payload::jsonb->>'counterparty' AND s.amount_cents=accounting.learning_money_cents(e.payload::jsonb->>'amount') AND NOT accounting.source_inactive(i.book_id,i.source_id)
 AND NOT accounting.revision_source_blocked(i.book_id,i.source_id) AND accounting.settlement_clearing_account(i.book_id,i.source_id)=i.clearing_account AND EXISTS(SELECT 1 FROM accounting.master_resolve(i.company_id,'counterparty',s.counterparty) p WHERE p.entity_id=i.counterparty_id))
$$;
CREATE FUNCTION accounting.settlement_open_item(target_book TEXT,target_source TEXT,target_party TEXT,target_version INTEGER,target_key TEXT,target_fp TEXT,target_reason TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE s RECORD; old RECORD; code TEXT; result TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Settlement reviewer scope required' USING ERRCODE='42501';END IF;
 SELECT * INTO old FROM accounting.settlement_items WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN IF old.book_id<>target_book OR old.source_id<>target_source OR old.counterparty_id<>target_party OR old.request_fingerprint<>target_fp THEN RAISE EXCEPTION 'Open item idempotency conflict';END IF;RETURN old.id;END IF;
 SELECT s.*,b.firm_id,b.company_id,p.status AS period_status,d.version AS draft_version,d.status AS draft_status,m.event_id,e.fact_version,e.status AS event_status,e.payload::jsonb AS facts INTO s FROM accounting.sources s JOIN accounting.books b ON b.id=s.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id JOIN accounting.business_mappings m ON m.source_id=s.id AND m.book_id=s.book_id JOIN accounting.business_events e ON e.id=m.event_id WHERE s.id=target_source AND s.book_id=target_book AND s.source_kind='business';
 code:=accounting.settlement_clearing_account(target_book,target_source);
 IF NOT FOUND OR s.period_status<>'open' OR s.draft_status<>'draft' OR s.draft_version<>target_version OR s.event_status<>'handed_off' OR s.facts->>'payment_status' IS DISTINCT FROM 'unpaid' OR code IS NULL OR s.amount_cents<>accounting.learning_money_cents(s.facts->>'amount') OR s.direction IS DISTINCT FROM s.facts->>'direction' OR s.counterparty IS DISTINCT FROM s.facts->>'counterparty' OR s.date IS DISTINCT FROM s.facts->>'occurred_on' OR accounting.source_inactive(target_book,target_source)
 OR accounting.revision_source_blocked(target_book,target_source) OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE event_id=s.event_id) OR NOT EXISTS(SELECT 1 FROM accounting.master_resolve(s.company_id,'counterparty',s.counterparty) q WHERE q.entity_id=target_party)
 OR length(target_key) NOT BETWEEN 8 AND 120 OR length(target_fp)<>64 OR length(trim(target_reason)) NOT BETWEEN 3 AND 1000 THEN RAISE EXCEPTION 'Current unpaid original and approved party required';END IF;
 result:=md5(random()::text||clock_timestamp()::text||target_source);
 INSERT INTO accounting.settlement_items(id,firm_id,company_id,book_id,source_id,event_id,fact_version,source_fingerprint,counterparty_id,counterparty_original,amount_cents,direction,clearing_account,created_by,created_at,idempotency_key,request_fingerprint,reason) VALUES(result,s.firm_id,s.company_id,target_book,s.id,s.event_id,s.fact_version,s.fingerprint,target_party,s.counterparty,s.amount_cents,s.direction,code,accounting.actor_user(),clock_timestamp()::text,target_key,target_fp,target_reason);
 RETURN result;END $$;
CREATE FUNCTION accounting.settlement_build_plan(target_book TEXT,body JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE bank RECORD; item RECORD; part JSONB; amount BIGINT; total BIGINT:=0; remaining BIGINT; party TEXT; pieces JSONB:='[]'; BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Settlement reviewer scope required' USING ERRCODE='42501';END IF;
 IF jsonb_typeof(body) IS DISTINCT FROM 'object' OR jsonb_typeof(body->'parts') IS DISTINCT FROM 'array' OR jsonb_array_length(body->'parts') NOT BETWEEN 1 AND 50 OR jsonb_typeof(body->'bank_source_id') IS DISTINCT FROM 'string' OR jsonb_typeof(body->'reason') IS DISTINCT FROM 'string' OR length(trim(COALESCE(body->>'reason',''))) NOT BETWEEN 3 AND 1000 OR EXISTS(SELECT 1 FROM jsonb_object_keys(body) k WHERE k NOT IN ('bank_source_id','parts','reason')) THEN RAISE EXCEPTION 'Invalid bounded allocation';END IF;
 SELECT s.*,b.company_id,p.status AS period_status,d.version AS draft_version,d.status AS draft_status INTO bank FROM accounting.sources s JOIN accounting.books b ON b.id=s.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=body->>'bank_source_id' AND s.book_id=target_book AND s.source_kind='bank';
 IF NOT FOUND OR bank.period_status<>'open' OR bank.draft_status<>'draft' OR accounting.source_inactive(target_book,bank.id) OR accounting.bank_source_import_blocked(target_book,bank.id) OR accounting.case_source_blocked(target_book,bank.id) OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE bank_source_id=bank.id) THEN RAISE EXCEPTION 'Current open unreviewed bank source required';END IF;
 SELECT entity_id INTO party FROM accounting.master_resolve(bank.company_id,'counterparty',bank.counterparty);IF party IS NULL THEN RAISE EXCEPTION 'Approved bank party mapping required';END IF;
 remaining:=bank.amount_cents-accounting.settlement_used(NULL,bank.id);
 IF (SELECT count(DISTINCT value->>'item_id') FROM jsonb_array_elements(body->'parts'))<>jsonb_array_length(body->'parts') THEN RAISE EXCEPTION 'Distinct items required';END IF;
 FOR part IN SELECT value FROM jsonb_array_elements(body->'parts') ORDER BY value->>'item_id' LOOP
  IF jsonb_typeof(part) IS DISTINCT FROM 'object' OR jsonb_typeof(part->'item_id') IS DISTINCT FROM 'string' OR jsonb_typeof(part->'amount') IS DISTINCT FROM 'string' OR EXISTS(SELECT 1 FROM jsonb_object_keys(part) k WHERE k NOT IN ('item_id','amount')) OR COALESCE(part->>'amount','') !~ '^(0|[1-9][0-9]{0,11})\.[0-9]{2}$' THEN RAISE EXCEPTION 'Exact cents required';END IF;
  amount:=accounting.learning_money_cents(part->>'amount');
  SELECT i.*,d.version AS draft_version,d.status AS draft_status INTO item FROM accounting.settlement_items i JOIN accounting.drafts d ON d.source_id=i.source_id AND d.book_id=i.book_id WHERE i.id=part->>'item_id' AND i.book_id=target_book AND i.firm_id=accounting.actor_firm();
  IF NOT FOUND OR NOT accounting.settlement_item_current(item.id) OR item.direction<>bank.direction OR item.counterparty_id<>party OR amount<=0 OR amount>item.amount_cents-accounting.settlement_used(item.id,NULL) OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE event_id=item.event_id) THEN RAISE EXCEPTION 'Scoped current item, party, direction and remaining balance required';END IF;
  pieces:=pieces||jsonb_build_array(jsonb_build_object('item_id',item.id,'amount_cents',amount,'remaining_before',item.amount_cents-accounting.settlement_used(item.id,NULL),'fact_version',item.fact_version,'clearing_account',item.clearing_account,'business_draft_version',item.draft_version,'business_draft_status',item.draft_status,'source_fingerprint',item.source_fingerprint));total:=total+amount;
 END LOOP;
 IF total>remaining THEN RAISE EXCEPTION 'Allocation exceeds bank remaining balance';END IF;
 RETURN jsonb_build_object('book_id',target_book,'bank_source_id',bank.id,'bank_fingerprint',bank.fingerprint,'bank_draft_version',bank.draft_version,'bank_remaining_before',remaining,'counterparty_id',party,'parts',pieces,'total_cents',total,'reason',body->>'reason');END $$;
CREATE FUNCTION accounting.settlement_make_preview(target_book TEXT,body TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE result TEXT; plan JSONB; b RECORD; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);plan:=accounting.settlement_build_plan(target_book,body::jsonb);SELECT * INTO b FROM accounting.books WHERE id=target_book;
 result:=md5(random()::text||clock_timestamp()::text||target_book);INSERT INTO accounting.settlement_previews VALUES(result,target_book,b.firm_id,b.company_id,accounting.actor_user(),body,plan::text,clock_timestamp()::text,(clock_timestamp()+interval '10 minutes')::text);RETURN result;END $$;
CREATE FUNCTION accounting.settlement_confirm(target_book TEXT,target_preview TEXT,target_key TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p RECORD; prior RECORD; body JSONB; part JSONB; result TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current settlement reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO p FROM accounting.settlement_previews WHERE id=target_preview AND book_id=target_book AND firm_id=accounting.actor_firm() AND created_by=accounting.actor_user();IF NOT FOUND THEN RAISE EXCEPTION 'Private preview scope required' USING ERRCODE='42501';END IF;
 SELECT * INTO prior FROM accounting.settlement_commands WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN IF prior.preview_id<>target_preview OR prior.book_id<>target_book THEN RAISE EXCEPTION 'Allocation key conflict';END IF;RETURN prior.id;END IF;
 body:=accounting.settlement_build_plan(target_book,p.request_json::jsonb);
 IF p.expires_at::timestamptz<clock_timestamp() OR body IS DISTINCT FROM p.plan_json::jsonb OR length(target_key) NOT BETWEEN 8 AND 120 THEN RAISE EXCEPTION 'Allocation preview expired or stale';END IF;
 result:=md5(random()::text||clock_timestamp()::text||target_preview);INSERT INTO accounting.settlement_commands VALUES(result,target_book,p.firm_id,p.company_id,target_preview,accounting.actor_user(),target_key,clock_timestamp()::text);
 FOR part IN SELECT value FROM jsonb_array_elements(body->'parts') LOOP
 INSERT INTO accounting.settlement_allocations VALUES(md5(random()::text||clock_timestamp()::text),result,target_book,p.firm_id,p.company_id,part->>'item_id',body->>'bank_source_id',(part->>'amount_cents')::bigint,part->>'clearing_account',accounting.actor_user(),clock_timestamp()::text);END LOOP;RETURN result;END $$;
CREATE FUNCTION accounting.settlement_allocation_fingerprint(target TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT encode(sha256(convert_to(jsonb_build_object('allocation_id',a.id,'bank_draft_version',d.version,'bank_draft_status',d.status,'amount_cents',a.amount_cents,'item_id',a.item_id,'reversed',EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id))::text,'UTF8')),'hex') FROM accounting.settlement_allocations a JOIN accounting.drafts d ON d.source_id=a.bank_source_id AND d.book_id=a.book_id WHERE a.id=target AND a.firm_id=accounting.actor_firm() AND accounting.ai_book_reviewer(a.book_id)
$$;
CREATE FUNCTION accounting.settlement_reverse(target_book TEXT,target TEXT,target_fp TEXT,target_key TEXT,target_reason TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE a RECORD; prior RECORD; result TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current settlement reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO a FROM accounting.settlement_allocations WHERE id=target AND book_id=target_book AND firm_id=accounting.actor_firm();IF NOT FOUND OR a.created_by=accounting.actor_user() THEN RAISE EXCEPTION 'Independent scoped reversal reviewer required' USING ERRCODE='42501';END IF;
 SELECT * INTO prior FROM accounting.settlement_reversals WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
 IF FOUND THEN IF prior.allocation_id<>target OR prior.expected_fingerprint<>target_fp OR prior.reason<>target_reason THEN RAISE EXCEPTION 'Reversal key conflict';END IF;RETURN prior.id;END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period JOIN accounting.drafts d ON d.book_id=b.id AND d.source_id=a.bank_source_id WHERE b.id=target_book AND p.status='open' AND d.status='draft') OR target_fp IS DISTINCT FROM accounting.settlement_allocation_fingerprint(target) OR length(target_key) NOT BETWEEN 8 AND 120 OR length(trim(target_reason)) NOT BETWEEN 3 AND 1000 THEN RAISE EXCEPTION 'Frozen bank evidence needs adjustment; stale reversal forbidden';END IF;
 result:=md5(random()::text||clock_timestamp()::text);INSERT INTO accounting.settlement_reversals VALUES(result,target,target_book,a.firm_id,a.company_id,accounting.actor_user(),clock_timestamp()::text,target_key,target_reason,target_fp);RETURN result;END $$;

CREATE FUNCTION accounting.settlement_bank_clearance_valid(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE bank RECORD; expected JSONB; actual JSONB; BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RETURN false;END IF;
 SELECT * INTO bank FROM accounting.sources WHERE id=target_source AND book_id=target_book;IF NOT FOUND OR bank.source_kind<>'bank' THEN RETURN true;END IF;
 IF accounting.settlement_used(NULL,target_source)=0 THEN RETURN true;END IF;
 IF accounting.settlement_used(NULL,target_source)<>bank.amount_cents OR EXISTS(
  SELECT 1 FROM accounting.settlement_allocations a JOIN accounting.settlement_items i ON i.id=a.item_id JOIN accounting.drafts d ON d.source_id=i.source_id AND d.book_id=i.book_id WHERE a.bank_source_id=target_source AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id) AND (d.status NOT IN ('approved','exported') OR NOT accounting.settlement_item_current(i.id) OR i.clearing_account<>a.clearing_account OR NOT EXISTS(SELECT 1 FROM accounting.master_resolve(i.company_id,'counterparty',bank.counterparty) q WHERE q.entity_id=i.counterparty_id))) THEN RETURN false;END IF;
 SELECT jsonb_object_agg(code,jsonb_build_array(debit,credit)) INTO expected FROM (
  SELECT a.clearing_account AS code,CASE WHEN bank.direction='expense' THEN sum(a.amount_cents) ELSE 0 END AS debit,CASE WHEN bank.direction='income' THEN sum(a.amount_cents) ELSE 0 END AS credit FROM accounting.settlement_allocations a WHERE a.bank_source_id=target_source AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id) GROUP BY a.clearing_account
  UNION ALL SELECT '1002',CASE WHEN bank.direction='income' THEN bank.amount_cents ELSE 0 END,CASE WHEN bank.direction='expense' THEN bank.amount_cents ELSE 0 END) expected_rows;
 SELECT jsonb_object_agg(code,jsonb_build_array(debit,credit)) INTO actual FROM (SELECT l.account_code AS code,sum(l.debit_cents) AS debit,sum(l.credit_cents) AS credit FROM accounting.draft_lines l JOIN accounting.drafts d ON d.id=l.draft_id AND d.book_id=l.book_id WHERE d.source_id=target_source AND d.book_id=target_book GROUP BY l.account_code) lines;
 RETURN actual IS NOT DISTINCT FROM expected;END $$;
CREATE FUNCTION accounting.settlement_workflow_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_TABLE_NAME='drafts' THEN
  IF NEW.status IN ('approved','exported') AND accounting.settlement_used(NULL,NEW.source_id)>0 AND NOT accounting.settlement_bank_clearance_valid(NEW.book_id,NEW.source_id) THEN RAISE EXCEPTION 'Bank allocation requires full reviewed original payable/receivable clearance';END IF;
 ELSIF TG_TABLE_NAME='ai_bank_links' THEN
  IF accounting.settlement_used(NULL,NEW.bank_source_id)>0 OR EXISTS(SELECT 1 FROM accounting.settlement_items i JOIN accounting.settlement_allocations a ON a.item_id=i.id WHERE i.event_id=NEW.event_id AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id)) THEN RAISE EXCEPTION 'Settlement and full bank matching are mutually exclusive';END IF;
 ELSE IF accounting.settlement_used(NULL,NEW.source_id)>0 THEN RAISE EXCEPTION 'Allocated bank source cannot be isolated';END IF;
 END IF;RETURN NEW;END $$;
CREATE TRIGGER settlement_draft_guard BEFORE UPDATE OF status ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.settlement_workflow_guard();
CREATE TRIGGER settlement_link_guard BEFORE INSERT ON accounting.ai_bank_links FOR EACH ROW EXECUTE FUNCTION accounting.settlement_workflow_guard();
CREATE TRIGGER settlement_disposition_guard BEFORE INSERT ON accounting.bank_source_dispositions FOR EACH ROW EXECUTE FUNCTION accounting.settlement_workflow_guard();
CREATE FUNCTION accounting.settlement_revision_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM accounting.settlement_items i JOIN accounting.settlement_allocations a ON a.item_id=i.id WHERE i.source_id=NEW.old_source_id AND i.book_id=NEW.old_book_id AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id)) THEN RAISE EXCEPTION 'Active allocations require independent reversal before replacing unpaid source facts';END IF;RETURN NEW;END $$;
CREATE TRIGGER settlement_revision_guard BEFORE INSERT ON accounting.fact_revision_lineage FOR EACH ROW EXECUTE FUNCTION accounting.settlement_revision_guard();
REVOKE ALL ON FUNCTION accounting.settlement_revision_guard() FROM PUBLIC,accounting_app,accounting_worker;
REVOKE ALL ON FUNCTION accounting.settlement_used(TEXT,TEXT),accounting.settlement_clearing_account(TEXT,TEXT),accounting.settlement_item_current(TEXT),accounting.settlement_open_item(TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT),accounting.settlement_build_plan(TEXT,JSONB),accounting.settlement_make_preview(TEXT,TEXT),accounting.settlement_confirm(TEXT,TEXT,TEXT),accounting.settlement_allocation_fingerprint(TEXT),accounting.settlement_reverse(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.settlement_bank_clearance_valid(TEXT,TEXT),accounting.settlement_workflow_guard() FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.settlement_open_item(TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT),accounting.settlement_make_preview(TEXT,TEXT),accounting.settlement_confirm(TEXT,TEXT,TEXT),accounting.settlement_allocation_fingerprint(TEXT),accounting.settlement_reverse(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.settlement_bank_clearance_valid(TEXT,TEXT) TO accounting_app;
