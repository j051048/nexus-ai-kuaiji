-- Evidence captured when an export is inserted cannot be manufactured later
-- from current accounts or an appendable audit event. No runtime can read or
-- write this side table; scoped capture compares the actual immutable CSV.
CREATE TABLE accounting.delivery_export_proofs(
 export_id TEXT PRIMARY KEY REFERENCES accounting.exports(id),transaction_id BIGINT NOT NULL,
 book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,period TEXT NOT NULL,
 catalog_json JSONB NOT NULL CHECK(jsonb_typeof(catalog_json)='array'),created_by TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
ALTER TABLE accounting.delivery_export_proofs ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.delivery_export_proofs FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.delivery_export_proofs FROM PUBLIC,accounting_app,accounting_worker;
CREATE TRIGGER delivery_export_proofs_immutable BEFORE UPDATE OR DELETE ON accounting.delivery_export_proofs
 FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
CREATE FUNCTION accounting.delivery_capture_export_proof() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE book RECORD; catalog JSONB; BEGIN
 -- Trusted administrative history restoration creates no current-txn proof.
 IF nullif(accounting.actor_user(),'') IS NULL THEN RETURN NEW; END IF;
 IF NEW.created_by IS DISTINCT FROM accounting.actor_user() OR NOT accounting.ai_book_reviewer(NEW.book_id)
 THEN RAISE EXCEPTION 'export proof requires current reviewer' USING ERRCODE='42501'; END IF;
 SELECT * INTO book FROM accounting.books WHERE id=NEW.book_id AND firm_id=accounting.actor_firm();
 IF NOT FOUND THEN RAISE EXCEPTION 'export proof scope denied' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('code',a.code,'name',a.name,'active',a.active,'version',a.version,'auxiliary_requirements',a.auxiliary_requirements) ORDER BY a.code),'[]'::jsonb)
  INTO catalog FROM accounting.accounts a WHERE a.book_id=book.id;
 INSERT INTO accounting.delivery_export_proofs(export_id,transaction_id,book_id,firm_id,company_id,period,catalog_json,created_by,created_at)
 VALUES(NEW.id,txid_current(),book.id,book.firm_id,book.company_id,book.period,catalog,accounting.actor_user(),clock_timestamp()::text);
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.delivery_capture_export_proof() FROM PUBLIC;
CREATE TRIGGER delivery_original_export_proof AFTER INSERT ON accounting.exports
 FOR EACH ROW EXECUTE FUNCTION accounting.delivery_capture_export_proof();

CREATE FUNCTION accounting.delivery_safe_csv_text(value TEXT) RETURNS TEXT LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT CASE WHEN left(ltrim(value,(SELECT string_agg(chr(code),'') FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288]) AS spaces(code))),1) IN ('=','+','-','@')
 OR left(value,1) IN (chr(9),chr(10),chr(13)) THEN chr(39)||value ELSE value END
$$;
CREATE FUNCTION accounting.delivery_csv_rows(document TEXT) RETURNS JSONB LANGUAGE plpgsql IMMUTABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE csv TEXT:=document; pattern TEXT:='("(?:[^"]|"")*"|[^,"\r\n]*)(,|\r\n|\n|\r|$)'; result JSONB; BEGIN
 IF csv IS NULL OR octet_length(csv)>20000000 THEN RETURN NULL; END IF;
 IF left(csv,1)=chr(65279) THEN csv:=substr(csv,2); END IF;
 IF csv='' OR regexp_replace(csv,pattern,'','g')<>'' THEN RETURN NULL; END IF;
 -- Tokenize full RFC4180 fields (including quoted commas/newlines/escaped
 -- quotes), then group by external record delimiters in a single SQL pass.
 WITH tokens AS (
  SELECT parts[1] AS raw,parts[2] AS delimiter,n,
   max(n) OVER() AS final_n FROM regexp_matches(csv,pattern,'g') WITH ORDINALITY AS matches(parts,n)
 ), useful AS (
  SELECT * FROM tokens WHERE NOT(n=final_n AND raw='' AND delimiter='' AND right(csv,1)<>',')
 ), numbered AS (
  SELECT *,COALESCE(sum(CASE WHEN delimiter IN (chr(10),chr(13),chr(13)||chr(10)) THEN 1 ELSE 0 END)
   OVER(ORDER BY n ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) AS record_number FROM useful
 ), records AS (
  SELECT record_number,jsonb_agg(CASE WHEN left(raw,1)='"' THEN replace(substr(raw,2,length(raw)-2),'""','"') ELSE raw END ORDER BY n) AS fields
   FROM numbered GROUP BY record_number
 ) SELECT COALESCE(jsonb_agg(fields ORDER BY record_number),'[]'::jsonb) INTO result FROM records;
 IF jsonb_array_length(result)>100001 THEN RETURN NULL; END IF;
 RETURN result;
 END $$;
REVOKE ALL ON FUNCTION accounting.delivery_safe_csv_text(TEXT),accounting.delivery_csv_rows(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.delivery_safe_csv_text(TEXT),accounting.delivery_csv_rows(TEXT) TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.capture_delivery_manifest(target_export TEXT,proposed TEXT,source_origin TEXT) RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE exported RECORD; book RECORD; export_proof RECORD; payload JSONB:=proposed::jsonb; item JSONB; draft RECORD; source RECORD;
  approval JSONB; lines JSONB; line JSONB; expected_items JSONB:='[]'::jsonb; expected_catalog JSONB;
  expected_payload JSONB; ids JSONB; actual_rows JSONB; expected_rows JSONB:='[]'::jsonb; expected_row JSONB;
  actual_data JSONB; expected_data JSONB; header JSONB; width INTEGER; audit_count INTEGER; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO exported FROM accounting.exports WHERE id=target_export;
 IF NOT FOUND OR NOT accounting.ai_book_reviewer(exported.book_id) THEN RAISE EXCEPTION 'current export review scope required' USING ERRCODE='42501'; END IF;
 SELECT * INTO book FROM accounting.books WHERE id=exported.book_id AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR jsonb_typeof(payload->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'items')<>exported.count
  OR exported.count NOT BETWEEN 1 AND 1000 OR source_origin NOT IN ('current_export','legacy_audit') THEN RAISE EXCEPTION 'manifest outside original export set'; END IF;
 IF EXISTS(SELECT 1 FROM accounting.delivery_manifests WHERE export_id=target_export) THEN RETURN; END IF;
 IF source_origin='current_export' THEN
  SELECT * INTO export_proof FROM accounting.delivery_export_proofs WHERE export_id=target_export;
  IF NOT FOUND OR export_proof.transaction_id<>txid_current() OR export_proof.created_by IS DISTINCT FROM accounting.actor_user()
   OR export_proof.book_id IS DISTINCT FROM book.id OR export_proof.firm_id IS DISTINCT FROM book.firm_id
   OR export_proof.company_id IS DISTINCT FROM book.company_id OR export_proof.period IS DISTINCT FROM book.period
  THEN RAISE EXCEPTION 'current export catalog proof requires the original export transaction'; END IF;
  expected_catalog:=export_proof.catalog_json;
 ELSE expected_catalog:='null'::jsonb; END IF;
 SELECT jsonb_agg(value->>'draft_id' ORDER BY value->>'draft_id') INTO ids FROM jsonb_array_elements(payload->'items');
 SELECT count(*) INTO audit_count FROM accounting.audit_events a WHERE a.book_id=book.id AND a.firm_id=book.firm_id AND a.action='draft.export' AND a.object_id=target_export;
 IF audit_count<>1 OR (SELECT count(DISTINCT value->>'draft_id') FROM jsonb_array_elements(payload->'items'))<>exported.count OR NOT EXISTS(
  SELECT 1 FROM accounting.audit_events a WHERE a.book_id=book.id AND a.firm_id=book.firm_id AND a.action='draft.export' AND a.object_id=target_export
  AND jsonb_typeof(a.details::jsonb->'draft_ids')='array'
  AND (SELECT jsonb_agg(value ORDER BY value) FROM jsonb_array_elements_text(a.details::jsonb->'draft_ids'))=ids)
 THEN RAISE EXCEPTION 'manifest requires unique canonical export selection audit'; END IF;
 actual_rows:=accounting.delivery_csv_rows(exported.csv);
 header:=jsonb_build_array('凭证ID','日期','摘要','科目编码','借方金额','贷方金额','币种','证据引用','辅助部门','辅助人员','辅助交易对方','辅助项目');
 IF actual_rows IS NULL OR jsonb_array_length(actual_rows)<2 OR (actual_rows->0 IS DISTINCT FROM header AND actual_rows->0 IS DISTINCT FROM jsonb_build_array('凭证ID','日期','摘要','科目编码','借方金额','贷方金额','币种','证据引用'))
 THEN RAISE EXCEPTION 'manifest requires a valid original RFC4180 export CSV'; END IF;
 width:=jsonb_array_length(actual_rows->0);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(actual_rows) r WHERE jsonb_array_length(r)<>width)
 THEN RAISE EXCEPTION 'historical CSV row width differs from original header'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(payload->'items') ORDER BY value->>'draft_id' LOOP
  SELECT * INTO draft FROM accounting.drafts WHERE id=item->>'draft_id' AND book_id=book.id AND status='exported';
  IF NOT FOUND THEN RAISE EXCEPTION 'manifest requires frozen exported journals'; END IF;
  SELECT * INTO source FROM accounting.sources WHERE id=draft.source_id AND book_id=book.id;
  IF NOT FOUND OR substr(source.date,1,7) IS DISTINCT FROM book.period THEN RAISE EXCEPTION 'manifest requires canonical period source'; END IF;
  SELECT count(*) INTO audit_count FROM accounting.audit_events a WHERE a.book_id=book.id AND a.firm_id=book.firm_id AND a.object_id=draft.id AND a.action='draft.approve';
  IF audit_count<>1 THEN RAISE EXCEPTION 'manifest requires unique original approval evidence'; END IF;
  SELECT a.details::jsonb INTO approval FROM accounting.audit_events a WHERE a.book_id=book.id AND a.firm_id=book.firm_id AND a.object_id=draft.id AND a.action='draft.approve';
  lines:=accounting.delivery_frozen_lines(book.id,draft.id);
  IF (approval->>'reviewed_version')::integer IS DISTINCT FROM draft.version-2 OR jsonb_typeof(approval->'evidence_ref') IS DISTINCT FROM 'string'
   OR approval->'lines' IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(value-'ordinal' ORDER BY (value->>'ordinal')::integer),'[]'::jsonb) FROM jsonb_array_elements(lines))
  THEN RAISE EXCEPTION 'manifest requires matching original approval audit'; END IF;
  expected_items:=expected_items||jsonb_build_array(jsonb_build_object('draft_id',draft.id,'source_id',source.id,'approved_version',draft.version-1,'exported_version',draft.version,
   'date',source.date,'description',draft.description,'amount',accounting.delivery_cents(source.amount_cents),'currency','CNY','source_fingerprint',source.fingerprint,'lines',lines));
  FOR line IN SELECT value FROM jsonb_array_elements(lines) LOOP
   expected_row:=jsonb_build_array(accounting.delivery_safe_csv_text(draft.id),source.date,accounting.delivery_safe_csv_text(draft.description),accounting.delivery_safe_csv_text(line->>'account_code'),
    line->>'debit',line->>'credit','CNY',accounting.delivery_safe_csv_text(approval->>'evidence_ref'));
   IF width=12 THEN
    expected_row:=expected_row||jsonb_build_array(accounting.delivery_safe_csv_text(COALESCE(line->'auxiliary_values'->>'department','')),accounting.delivery_safe_csv_text(COALESCE(line->'auxiliary_values'->>'employee','')),
     accounting.delivery_safe_csv_text(COALESCE(line->'auxiliary_values'->>'counterparty','')),accounting.delivery_safe_csv_text(COALESCE(line->'auxiliary_values'->>'project','')));
   ELSIF line->'auxiliary_values' IS DISTINCT FROM '{}'::jsonb THEN RAISE EXCEPTION 'historical CSV cannot prove auxiliary values'; END IF;
   expected_rows:=expected_rows||jsonb_build_array(expected_row);
  END LOOP;
 END LOOP;
 SELECT COALESCE(jsonb_agg(value ORDER BY value::text),'[]'::jsonb) INTO actual_data FROM jsonb_array_elements(actual_rows) WITH ORDINALITY AS actual(value,n) WHERE n>1;
 SELECT COALESCE(jsonb_agg(value ORDER BY value::text),'[]'::jsonb) INTO expected_data FROM jsonb_array_elements(expected_rows);
 IF actual_data IS DISTINCT FROM expected_data THEN RAISE EXCEPTION 'manifest journals do not match the actual original export CSV'; END IF;
 expected_payload:=jsonb_build_object('version',1,'export_id',target_export,'book_id',book.id,'company_id',book.company_id,'period',book.period,'currency','CNY','catalog',expected_catalog,'items',expected_items);
 IF payload IS DISTINCT FROM expected_payload THEN RAISE EXCEPTION 'manifest payload differs from canonical frozen export facts'; END IF;
 INSERT INTO accounting.delivery_manifests(export_id,book_id,firm_id,company_id,period,origin,payload_json,created_by,created_at)
  VALUES(target_export,book.id,book.firm_id,book.company_id,book.period,source_origin,expected_payload::text,accounting.actor_user(),clock_timestamp()::text);
 END $$;
REVOKE ALL ON FUNCTION accounting.capture_delivery_manifest(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.capture_delivery_manifest(TEXT,TEXT,TEXT) TO accounting_app;

-- Keep 034 at its originally applied bytes; shape hardening belongs to this
-- forward migration so stored receipt facts cannot crash later validation.
CREATE OR REPLACE FUNCTION accounting.delivery_receipt_facts_guard() RETURNS TRIGGER LANGUAGE plpgsql
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
    IF line ? 'ordinal' AND jsonb_typeof(line->'ordinal')<>'null' AND
      (jsonb_typeof(line->'ordinal')<>'number' OR COALESCE(line->>'ordinal','') !~ '^[0-9]{1,2}$') THEN RAISE EXCEPTION 'invalid manual receipt line ordinal'; END IF;
    FOREACH key IN ARRAY ARRAY['account_code','debit','credit'] LOOP
     IF line ? key AND jsonb_typeof(line->key) NOT IN ('string','null') THEN RAISE EXCEPTION 'invalid manual receipt line amount/code'; END IF;
    END LOOP;
   END LOOP;
  END IF;
 END LOOP;
 RETURN NEW; END $$;
