-- Offline reference files and frozen intents; no live-send state is writable.
CREATE UNIQUE INDEX idx_bridge_manifest_scope ON accounting.delivery_manifests(export_id,book_id);
CREATE TABLE accounting.bridge_configurations(
 ledger_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb) IN ('object','array')),request_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,version),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.bridge_import_previews(
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL,configuration_version INTEGER NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 records_json TEXT NOT NULL CHECK(jsonb_typeof(records_json::jsonb) IN ('object','array')),record_count INTEGER NOT NULL CHECK(record_count BETWEEN 1 AND 1000),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 idempotency_key TEXT NOT NULL,reason TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('preview','confirmed')),
 UNIQUE(firm_id,created_by,idempotency_key),UNIQUE(id,book_id,firm_id,company_id),
 FOREIGN KEY(ledger_id,configuration_version) REFERENCES accounting.bridge_configurations(ledger_id,version),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.bridge_reference_records(
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,preview_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('account','counterparty','project','bank_account','voucher','balance')),external_id TEXT NOT NULL,period TEXT NOT NULL,record_json TEXT NOT NULL CHECK(jsonb_typeof(record_json::jsonb) IN ('object','array')),created_at TEXT NOT NULL,
 UNIQUE(ledger_id,kind,external_id,period),FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES accounting.bridge_import_previews(id,book_id,firm_id,company_id),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
CREATE TABLE accounting.bridge_outbox(
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL,configuration_version INTEGER NOT NULL,export_id TEXT NOT NULL UNIQUE,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 external_code TEXT NOT NULL UNIQUE,package_json TEXT NOT NULL CHECK(jsonb_typeof(package_json::jsonb) IN ('object','array')),request_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,reason TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,state TEXT NOT NULL CHECK(state='awaiting_adapter'),
 UNIQUE(firm_id,created_by,idempotency_key),FOREIGN KEY(export_id,book_id) REFERENCES accounting.delivery_manifests(export_id,book_id),
 FOREIGN KEY(ledger_id,configuration_version) REFERENCES accounting.bridge_configurations(ledger_id,version),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE INDEX idx_bridge_previews_book ON accounting.bridge_import_previews(book_id,created_at,id);
CREATE INDEX idx_bridge_outbox_book ON accounting.bridge_outbox(book_id,created_at,id);

CREATE FUNCTION accounting.bridge_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE config JSONB; previous JSONB; item JSONB; original JSONB; ln JSONB; mapped TEXT; expected_lines JSONB; required_count INTEGER;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Bridge evidence is immutable'; END IF;
 IF NOT accounting.ai_book_reviewer(NEW.book_id) OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
  OR NOT EXISTS(SELECT 1 FROM accounting.ledger_periods WHERE book_id=NEW.book_id AND ledger_id=NEW.ledger_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id)
 THEN RAISE EXCEPTION 'Current book and ledger scope required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bridge_configurations' THEN
  IF TG_OP<>'INSERT' OR NEW.created_by IS DISTINCT FROM accounting.actor_user() OR NOT accounting.ledger_manage(NEW.ledger_id)
   OR NEW.version<>(SELECT COALESCE(MAX(version),0)+1 FROM accounting.bridge_configurations WHERE ledger_id=NEW.ledger_id)
  THEN RAISE EXCEPTION 'Current ledger manager and new configuration version required' USING ERRCODE='42501'; END IF;
  config:=NEW.payload_json::jsonb;
  IF jsonb_typeof(config->'accounts') IS DISTINCT FROM 'array' OR jsonb_array_length(config->'accounts') NOT BETWEEN 1 AND 1000
   OR jsonb_typeof(config->'auxiliaries') IS DISTINCT FROM 'array' OR jsonb_array_length(config->'auxiliaries')>2000
   OR length(COALESCE(config->>'external_book_id','')) NOT BETWEEN 1 AND 100
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(config) k WHERE k NOT IN ('external_book_id','product_version','deployment','accounts','auxiliaries','reason'))
  THEN RAISE EXCEPTION 'Bounded offline configuration required'; END IF;
  IF (SELECT COUNT(DISTINCT value->>'local_code') FROM jsonb_array_elements(config->'accounts'))<>jsonb_array_length(config->'accounts')
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(config->'accounts') x WHERE length(COALESCE(x->>'external_code','')) NOT BETWEEN 1 AND 100 OR NOT EXISTS(SELECT 1 FROM accounting.accounts a WHERE a.book_id=NEW.book_id AND a.code=x->>'local_code' AND a.active=1))
  THEN RAISE EXCEPTION 'Current local account mappings required'; END IF;
  IF (SELECT COUNT(DISTINCT (x->>'kind',x->>'local_value')) FROM jsonb_array_elements(config->'auxiliaries') x)<>jsonb_array_length(config->'auxiliaries')
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(config->'auxiliaries') x WHERE COALESCE(x->>'kind','') NOT IN ('department','employee','counterparty','project')
     OR jsonb_typeof(x->'local_value') IS DISTINCT FROM 'string' OR length(trim(COALESCE(x->>'local_value',''))) NOT BETWEEN 1 AND 200
     OR jsonb_typeof(x->'external_code') IS DISTINCT FROM 'string' OR length(trim(COALESCE(x->>'external_code',''))) NOT BETWEEN 1 AND 100)
  THEN RAISE EXCEPTION 'Unique bounded auxiliary mappings required'; END IF;
  SELECT payload_json::jsonb INTO previous FROM accounting.bridge_configurations WHERE ledger_id=NEW.ledger_id ORDER BY version DESC LIMIT 1;
  IF previous IS NOT NULL AND previous->>'external_book_id' IS DISTINCT FROM config->>'external_book_id'
   AND (EXISTS(SELECT 1 FROM accounting.bridge_reference_records WHERE ledger_id=NEW.ledger_id) OR EXISTS(SELECT 1 FROM accounting.bridge_outbox WHERE ledger_id=NEW.ledger_id))
  THEN RAISE EXCEPTION 'Used external book identity cannot be rebound'; END IF;
 ELSIF TG_TABLE_NAME='bridge_import_previews' THEN
  IF TG_OP='UPDATE' THEN
   IF OLD.state<>'preview' OR NEW.state<>'confirmed' OR (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN RAISE EXCEPTION 'Immutable import preview'; END IF;
   IF NEW.configuration_version<>(SELECT MAX(version) FROM accounting.bridge_configurations WHERE ledger_id=NEW.ledger_id)
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.records_json::jsonb) r WHERE NOT EXISTS(SELECT 1 FROM accounting.bridge_reference_records q WHERE q.ledger_id=NEW.ledger_id AND q.kind=r->>'kind' AND q.external_id=r->>'external_id' AND q.period=r->>'period' AND q.record_json::jsonb=r))
   THEN RAISE EXCEPTION 'All current preview facts must be retained before confirmation'; END IF;
  ELSE
   IF NEW.created_by IS DISTINCT FROM accounting.actor_user() OR NEW.state<>'preview' OR jsonb_typeof(NEW.records_json::jsonb) IS DISTINCT FROM 'array'
    OR jsonb_array_length(NEW.records_json::jsonb)<>NEW.record_count OR octet_length(NEW.records_json)>2000000
    OR NEW.configuration_version<>(SELECT MAX(version) FROM accounting.bridge_configurations WHERE ledger_id=NEW.ledger_id)
   THEN RAISE EXCEPTION 'Bounded current preview required'; END IF;
   FOR item IN SELECT value FROM jsonb_array_elements(NEW.records_json::jsonb) LOOP
    IF item->>'period' IS DISTINCT FROM (SELECT period FROM accounting.books WHERE id=NEW.book_id)
     OR COALESCE(item->>'kind','') NOT IN ('account','counterparty','project','bank_account','voucher','balance')
     OR length(COALESCE(item->>'external_id','')) NOT BETWEEN 1 AND 100 OR length(COALESCE(item->>'source_ref','')) NOT BETWEEN 1 AND 500
     OR jsonb_typeof(item->'debit') IS DISTINCT FROM 'string' OR jsonb_typeof(item->'credit') IS DISTINCT FROM 'string'
    THEN RAISE EXCEPTION 'Invalid reference facts'; END IF;
    PERFORM accounting.domain_money_cents(item->>'debit'),accounting.domain_money_cents(item->>'credit');
   END LOOP;
  END IF;
 ELSIF TG_TABLE_NAME='bridge_reference_records' THEN
  IF TG_OP<>'INSERT' OR NOT EXISTS(SELECT 1 FROM accounting.bridge_import_previews p CROSS JOIN LATERAL jsonb_array_elements(p.records_json::jsonb) r WHERE p.id=NEW.preview_id AND p.book_id=NEW.book_id AND p.ledger_id=NEW.ledger_id AND p.state='preview' AND p.configuration_version=(SELECT MAX(version) FROM accounting.bridge_configurations WHERE ledger_id=NEW.ledger_id) AND r=NEW.record_json::jsonb AND r->>'kind'=NEW.kind AND r->>'external_id'=NEW.external_id AND r->>'period'=NEW.period)
  THEN RAISE EXCEPTION 'Original record must be bound to the current preview'; END IF;
 ELSE
  IF TG_OP<>'INSERT' OR NEW.created_by IS DISTINCT FROM accounting.actor_user() OR NEW.state<>'awaiting_adapter'
   OR NEW.configuration_version<>(SELECT MAX(version) FROM accounting.bridge_configurations WHERE ledger_id=NEW.ledger_id)
   OR NEW.external_code IS DISTINCT FROM substr(encode(sha256(convert_to('{"book":'||to_json(NEW.book_id)::TEXT||',"export":'||to_json(NEW.export_id)::TEXT||'}','UTF8')),'hex'),1,40)
  THEN RAISE EXCEPTION 'Only frozen offline intent may be created'; END IF;
  config:=NEW.package_json::jsonb;
  SELECT payload_json::jsonb INTO original FROM accounting.delivery_manifests WHERE export_id=NEW.export_id AND book_id=NEW.book_id;
  SELECT payload_json::jsonb INTO previous FROM accounting.bridge_configurations WHERE ledger_id=NEW.ledger_id AND version=NEW.configuration_version;
  IF original IS NULL OR config->>'schema' IS DISTINCT FROM 'hengzhang.tplus.delivery.v1' OR config->'live_enabled' IS DISTINCT FROM 'false'::jsonb
   OR config->>'export_id' IS DISTINCT FROM NEW.export_id OR config->>'book_id' IS DISTINCT FROM NEW.book_id OR config->>'company_id' IS DISTINCT FROM NEW.company_id
   OR config->>'period' IS DISTINCT FROM original->>'period' OR config->>'external_book_id' IS DISTINCT FROM previous->>'external_book_id'
   OR config->>'provider' IS DISTINCT FROM 'chanjet_tplus' OR config->>'configuration_version' IS DISTINCT FROM NEW.configuration_version::TEXT
   OR jsonb_typeof(config->'vouchers') IS DISTINCT FROM 'array' OR jsonb_array_length(config->'vouchers')<>jsonb_array_length(original->'items')
   OR (SELECT count(DISTINCT value->>'draft_id') FROM jsonb_array_elements(config->'vouchers'))<>jsonb_array_length(original->'items')
  THEN RAISE EXCEPTION 'Package must exactly bind a frozen export'; END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(config->'vouchers') LOOP
   SELECT value INTO original FROM jsonb_array_elements((SELECT payload_json::jsonb->'items' FROM accounting.delivery_manifests WHERE export_id=NEW.export_id)) WHERE value->>'draft_id'=item->>'draft_id';
   IF original IS NULL OR (item-'lines'-'external_code') IS DISTINCT FROM (original-'lines')
    OR item->>'external_code' IS DISTINCT FROM substr(encode(sha256(convert_to('{"book":'||to_json(NEW.book_id)::TEXT||',"draft":'||to_json(item->>'draft_id')::TEXT||'}','UTF8')),'hex'),1,40)
   THEN RAISE EXCEPTION 'Voucher differs from frozen export'; END IF;
   expected_lines:='[]'::jsonb;
   FOR ln IN SELECT value FROM jsonb_array_elements(original->'lines') LOOP
    SELECT value->>'external_code' INTO mapped FROM jsonb_array_elements(previous->'accounts') WHERE value->>'local_code'=ln->>'account_code';
    IF mapped IS NULL THEN RAISE EXCEPTION 'Account mapping missing'; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_each_text(ln->'auxiliary_values') a WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(previous->'auxiliaries') x WHERE x->>'kind'=a.key AND x->>'local_value'=a.value)) THEN RAISE EXCEPTION 'Auxiliary mapping missing'; END IF;
    expected_lines:=expected_lines||jsonb_build_array(ln||jsonb_build_object('local_account_code',ln->>'account_code','account_code',mapped,'auxiliary_values',COALESCE((SELECT jsonb_object_agg(a.key,x->>'external_code') FROM jsonb_each_text(ln->'auxiliary_values') a JOIN LATERAL jsonb_array_elements(previous->'auxiliaries') x ON x->>'kind'=a.key AND x->>'local_value'=a.value),'{}'::jsonb)));
   END LOOP;
   IF item->'lines' IS DISTINCT FROM expected_lines THEN RAISE EXCEPTION 'Mapped lines differ from frozen export'; END IF;
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t TEXT; BEGIN
 FOREACH t IN ARRAY ARRAY['bridge_configurations','bridge_import_previews','bridge_reference_records','bridge_outbox'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY bridge_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND %s)',t,CASE WHEN t='bridge_configurations' THEN 'accounting.ledger_visible(ledger_id)' ELSE 'accounting.book_allowed(book_id)' END);
  EXECUTE format('CREATE POLICY bridge_insert ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND accounting.ai_book_reviewer(book_id))',t);
  EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',t);
  EXECUTE format('CREATE TRIGGER bridge_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.bridge_scope_guard()',t);
 END LOOP;
END $$;
CREATE POLICY bridge_confirm ON accounting.bridge_import_previews FOR UPDATE TO accounting_app USING(accounting.ai_book_reviewer(book_id)) WITH CHECK(accounting.ai_book_reviewer(book_id));
GRANT UPDATE(state) ON accounting.bridge_import_previews TO accounting_app;
REVOKE ALL ON FUNCTION accounting.bridge_scope_guard() FROM PUBLIC;
CREATE FUNCTION accounting.bridge_binding_used(target_ledger TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.ledger_manage(target_ledger) THEN RAISE EXCEPTION 'Ledger scope denied' USING ERRCODE='42501'; END IF;
 RETURN EXISTS(SELECT 1 FROM accounting.bridge_reference_records WHERE ledger_id=target_ledger) OR EXISTS(SELECT 1 FROM accounting.bridge_outbox WHERE ledger_id=target_ledger);
END $$;
REVOKE ALL ON FUNCTION accounting.bridge_binding_used(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.bridge_binding_used(TEXT) TO accounting_app;
