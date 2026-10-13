-- Manual receipt facts never carry a persisted `verified`/journal mutation flag.
-- Verification is derived from an immutable, server-checked export manifest.
CREATE UNIQUE INDEX idx_exports_delivery_scope ON accounting.exports(id,book_id);
CREATE TABLE accounting.delivery_manifests(
 export_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,period TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('current_export','legacy_audit')),payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb)='object'),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(export_id,book_id) REFERENCES accounting.exports(id,book_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.delivery_receipts(
 id TEXT PRIMARY KEY,export_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 body_json TEXT NOT NULL CHECK(jsonb_typeof(body_json::jsonb)='object'),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),idempotency_key TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),import_format TEXT NOT NULL CHECK(import_format IN ('json','csv','structured')),raw_hash TEXT NOT NULL CHECK(length(raw_hash)=64),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(export_id,book_id) REFERENCES accounting.exports(id,book_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.delivery_receipt_items(
 receipt_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 999),
 claimed_draft_id TEXT NOT NULL,external_voucher_id TEXT,raw_json TEXT NOT NULL CHECK(jsonb_typeof(raw_json::jsonb)='object'),PRIMARY KEY(receipt_id,ordinal),
 FOREIGN KEY(receipt_id,book_id,firm_id,company_id) REFERENCES accounting.delivery_receipts(id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.delivery_notes(
 id TEXT PRIMARY KEY,receipt_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 draft_id TEXT NOT NULL,issue_key TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),idempotency_key TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(receipt_id,book_id,firm_id,company_id) REFERENCES accounting.delivery_receipts(id,book_id,firm_id,company_id)
);
CREATE INDEX idx_delivery_receipts_page ON accounting.delivery_receipts(book_id,export_id,created_at,id);
CREATE INDEX idx_delivery_items_draft ON accounting.delivery_receipt_items(book_id,claimed_draft_id,external_voucher_id);
CREATE INDEX idx_delivery_items_external ON accounting.delivery_receipt_items(book_id,external_voucher_id,claimed_draft_id);
CREATE INDEX idx_delivery_notes ON accounting.delivery_notes(book_id,receipt_id,draft_id);

CREATE FUNCTION accounting.delivery_cents(value BIGINT) RETURNS TEXT LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT (value/100)::text||'.'||lpad((value%100)::text,2,'0') $$;
CREATE FUNCTION accounting.delivery_frozen_lines(target_book TEXT,target_draft TEXT) RETURNS JSONB LANGUAGE sql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('ordinal',l.ordinal,'account_code',l.account_code,
  'debit',accounting.delivery_cents(l.debit_cents),'credit',accounting.delivery_cents(l.credit_cents),
  'auxiliary_values',COALESCE(a.values_json::jsonb,'{}'::jsonb)) ORDER BY l.ordinal),'[]'::jsonb)
 FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal
 WHERE l.book_id=target_book AND l.draft_id=target_draft AND accounting.book_allowed(target_book)
$$;
CREATE FUNCTION accounting.capture_delivery_manifest(target_export TEXT,proposed TEXT,source_origin TEXT) RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE exported RECORD; book RECORD; payload JSONB:=proposed::jsonb; item JSONB; draft RECORD; source RECORD;
  expected_items JSONB:='[]'::jsonb; expected_catalog JSONB; expected_payload JSONB; ids JSONB; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO exported FROM accounting.exports WHERE id=target_export;
 IF NOT FOUND OR NOT accounting.ai_book_reviewer(exported.book_id) THEN RAISE EXCEPTION 'current export review scope required' USING ERRCODE='42501'; END IF;
 SELECT * INTO book FROM accounting.books WHERE id=exported.book_id AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR jsonb_typeof(payload->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'items')<>exported.count
  OR exported.count NOT BETWEEN 1 AND 1000 OR source_origin NOT IN ('current_export','legacy_audit') THEN RAISE EXCEPTION 'manifest outside original export set'; END IF;
 IF EXISTS(SELECT 1 FROM accounting.delivery_manifests WHERE export_id=target_export) THEN RETURN; END IF;
 SELECT jsonb_agg(value->>'draft_id' ORDER BY value->>'draft_id') INTO ids FROM jsonb_array_elements(payload->'items');
 IF (SELECT count(DISTINCT value->>'draft_id') FROM jsonb_array_elements(payload->'items'))<>exported.count OR NOT EXISTS(
  SELECT 1 FROM accounting.audit_events a WHERE a.book_id=book.id AND a.firm_id=book.firm_id AND a.action='draft.export' AND a.object_id=target_export
  AND (SELECT jsonb_agg(value ORDER BY value) FROM jsonb_array_elements_text(a.details::jsonb->'draft_ids'))=ids)
 THEN RAISE EXCEPTION 'manifest requires immutable canonical export selection audit'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(payload->'items') ORDER BY value->>'draft_id' LOOP
  SELECT * INTO draft FROM accounting.drafts WHERE id=item->>'draft_id' AND book_id=book.id AND status='exported';
  IF NOT FOUND THEN RAISE EXCEPTION 'manifest requires frozen exported journals'; END IF;
  SELECT * INTO source FROM accounting.sources WHERE id=draft.source_id AND book_id=book.id;
  IF NOT FOUND THEN RAISE EXCEPTION 'manifest requires canonical source'; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.audit_events a WHERE a.book_id=book.id AND a.firm_id=book.firm_id AND a.object_id=draft.id AND a.action='draft.approve'
   AND (a.details::jsonb->>'reviewed_version')::integer=draft.version-2
   AND a.details::jsonb->'lines'=(SELECT COALESCE(jsonb_agg(value-'ordinal' ORDER BY (value->>'ordinal')::integer),'[]'::jsonb) FROM jsonb_array_elements(accounting.delivery_frozen_lines(book.id,draft.id))))
  THEN RAISE EXCEPTION 'manifest requires matching original approval audit'; END IF;
  expected_items:=expected_items||jsonb_build_array(jsonb_build_object('draft_id',draft.id,'source_id',source.id,'approved_version',draft.version-1,'exported_version',draft.version,
   'date',source.date,'description',draft.description,'amount',accounting.delivery_cents(source.amount_cents),'currency','CNY','source_fingerprint',source.fingerprint,
   'lines',accounting.delivery_frozen_lines(book.id,draft.id)));
 END LOOP;
 IF source_origin='current_export' THEN
  -- Current catalog must be captured as part of the original delivery txn.
  IF exported.created_by<>accounting.actor_user() OR exported.created_at::timestamptz<clock_timestamp()-interval '60 seconds' THEN RAISE EXCEPTION 'historical export cannot use a current catalog snapshot'; END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('code',a.code,'name',a.name,'active',a.active,'version',a.version,'auxiliary_requirements',a.auxiliary_requirements) ORDER BY a.code),'[]'::jsonb)
   INTO expected_catalog FROM accounting.accounts a WHERE a.book_id=book.id;
 ELSE
  -- Legacy lineage may be reconstructed, but historical catalog is unknown.
  expected_catalog:='null'::jsonb;
 END IF;
 expected_payload:=jsonb_build_object('version',1,'export_id',target_export,'book_id',book.id,'company_id',book.company_id,'period',book.period,'currency','CNY','catalog',expected_catalog,'items',expected_items);
 IF payload IS DISTINCT FROM expected_payload THEN RAISE EXCEPTION 'manifest payload differs from canonical frozen export facts'; END IF;
 INSERT INTO accounting.delivery_manifests(export_id,book_id,firm_id,company_id,period,origin,payload_json,created_by,created_at)
  VALUES(target_export,book.id,book.firm_id,book.company_id,book.period,source_origin,expected_payload::text,accounting.actor_user(),clock_timestamp()::text);
 END $$;
CREATE FUNCTION accounting.delivery_immutable() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN RAISE EXCEPTION 'delivery evidence is append only'; END $$;
CREATE FUNCTION accounting.delivery_receipt_facts_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE item JSONB; line JSONB; key TEXT; facts JSONB:=NEW.body_json::jsonb; BEGIN
 IF octet_length(NEW.body_json)>2000000 OR jsonb_typeof(facts->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(facts->'items') NOT BETWEEN 1 AND 1000
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(facts) k WHERE k NOT IN ('book_id','company_id','period','currency','items')) THEN RAISE EXCEPTION 'invalid manual receipt envelope'; END IF;
 FOREACH key IN ARRAY ARRAY['book_id','company_id','period','currency'] LOOP
  IF facts ? key AND jsonb_typeof(facts->key) NOT IN ('string','null') THEN RAISE EXCEPTION 'invalid manual receipt scope claim'; END IF;
 END LOOP;
 FOR item IN SELECT value FROM jsonb_array_elements(facts->'items') LOOP
  IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR jsonb_typeof(item->'draft_id') IS DISTINCT FROM 'string' OR length(item->>'draft_id') NOT BETWEEN 1 AND 100
   OR COALESCE(item->>'status','') NOT IN ('accepted','rejected','unknown')
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN ('draft_id','external_voucher_id','status','date','amount','description','lines'))
   OR (item ? 'lines' AND jsonb_typeof(item->'lines') NOT IN ('array','null')) THEN RAISE EXCEPTION 'invalid manual receipt voucher facts'; END IF;
  FOREACH key IN ARRAY ARRAY['external_voucher_id','date','amount','description'] LOOP
   IF item ? key AND jsonb_typeof(item->key) NOT IN ('string','null') THEN RAISE EXCEPTION 'invalid manual receipt text fact'; END IF;
  END LOOP;
  IF jsonb_typeof(item->'lines')='array' THEN
   IF jsonb_array_length(item->'lines')>100 THEN RAISE EXCEPTION 'manual receipt line bound'; END IF;
   FOR line IN SELECT value FROM jsonb_array_elements(item->'lines') LOOP
    IF jsonb_typeof(line) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(line) k WHERE k NOT IN ('ordinal','account_code','debit','credit','auxiliary_values'))
     OR (line ? 'auxiliary_values' AND jsonb_typeof(line->'auxiliary_values') NOT IN ('object','null')) THEN RAISE EXCEPTION 'invalid manual receipt line facts'; END IF;
   END LOOP;
  END IF;
 END LOOP;
 RETURN NEW; END $$;
CREATE FUNCTION accounting.delivery_item_facts_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.delivery_receipts r WHERE r.id=NEW.receipt_id AND r.book_id=NEW.book_id AND r.firm_id=NEW.firm_id AND r.company_id=NEW.company_id
  AND r.body_json::jsonb->'items'->NEW.ordinal=NEW.raw_json::jsonb
  AND r.body_json::jsonb->'items'->NEW.ordinal->>'draft_id'=NEW.claimed_draft_id
  AND (r.body_json::jsonb->'items'->NEW.ordinal->>'external_voucher_id') IS NOT DISTINCT FROM NEW.external_voucher_id)
 THEN RAISE EXCEPTION 'receipt index must match exact immutable receipt facts'; END IF;
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.delivery_cents(BIGINT),accounting.delivery_frozen_lines(TEXT,TEXT),accounting.capture_delivery_manifest(TEXT,TEXT,TEXT),accounting.delivery_immutable(),accounting.delivery_receipt_facts_guard(),accounting.delivery_item_facts_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.delivery_cents(BIGINT),accounting.delivery_frozen_lines(TEXT,TEXT),accounting.capture_delivery_manifest(TEXT,TEXT,TEXT) TO accounting_app;
CREATE TRIGGER delivery_receipt_facts BEFORE INSERT ON accounting.delivery_receipts FOR EACH ROW EXECUTE FUNCTION accounting.delivery_receipt_facts_guard();
CREATE TRIGGER delivery_item_facts BEFORE INSERT ON accounting.delivery_receipt_items FOR EACH ROW EXECUTE FUNCTION accounting.delivery_item_facts_guard();
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['delivery_manifests','delivery_receipts','delivery_receipt_items','delivery_notes'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE TRIGGER delivery_no_mutation BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable()',item);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',item);
  EXECUTE format('GRANT SELECT ON accounting.%I TO accounting_app',item);
  EXECUTE format('CREATE POLICY delivery_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id))',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['delivery_receipts','delivery_receipt_items','delivery_notes'] LOOP
  EXECUTE format('GRANT INSERT ON accounting.%I TO accounting_app',item);
  IF item='delivery_receipt_items' THEN
   EXECUTE format('CREATE POLICY delivery_append ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND accounting.ai_book_reviewer(book_id))',item);
  ELSE
   EXECUTE format('CREATE POLICY delivery_append ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.ai_book_reviewer(book_id))',item);
  END IF;
 END LOOP;
END $$;
