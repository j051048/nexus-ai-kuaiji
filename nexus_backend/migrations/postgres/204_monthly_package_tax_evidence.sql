-- compatibility: incremental
-- Monthly package schema v2 adds tax_filings.json; v1 stays exactly 11 files.
SET LOCAL search_path=accounting,pg_catalog;
CREATE OR REPLACE FUNCTION accounting.monthly_delivery_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE b RECORD; payload JSONB; p RECORD; entry JSONB; file_text TEXT; schema_version TEXT; expected_files INTEGER;
 BEGIN
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR b.company_id IS DISTINCT FROM NEW.company_id
 OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NEW.created_by IS DISTINCT FROM accounting.actor_user()
 OR NOT (accounting.book_allowed(NEW.book_id) OR (TG_TABLE_NAME='monthly_delivery_acknowledgements' AND accounting.recipient_package_allowed(to_jsonb(NEW)->>'package_id'))) THEN
 RAISE EXCEPTION 'Current monthly delivery scope required' USING ERRCODE='42501'; END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
 IF TG_TABLE_NAME='monthly_delivery_acknowledgements' THEN
 SELECT * INTO p FROM accounting.monthly_delivery_packages WHERE id=NEW.package_id AND book_id=NEW.book_id;
 IF NOT FOUND OR p.manifest_sha256 IS DISTINCT FROM NEW.manifest_sha256
 OR p.contact_user_id IS DISTINCT FROM NEW.created_by
 OR p.created_by=NEW.created_by
 OR NOT accounting.monthly_delivery_contact_eligible(NEW.book_id,NEW.created_by) THEN
 RAISE EXCEPTION 'Exact package hash and current client contact required' USING ERRCODE='42501'; END IF;
 ELSE
 IF NOT accounting.book_review_allowed(NEW.book_id)
 OR NOT accounting.monthly_delivery_contact_eligible(NEW.book_id,NEW.contact_user_id) THEN
 RAISE EXCEPTION 'Current reviewer and eligible client contact required' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.report_runs r
 JOIN accounting.internal_period_locks l ON l.book_id=r.book_id AND l.period=r.period
 JOIN accounting.service_plan_versions s ON s.book_id=r.book_id AND s.version=NEW.service_plan_version
 WHERE r.id=NEW.report_id AND r.book_id=NEW.book_id AND r.version=NEW.report_version AND r.period=NEW.period
 AND r.status='published' AND r.reviewed_by IS NOT NULL AND r.reviewed_by<>r.created_by AND r.published_at IS NOT NULL
 AND s.contact_user_id=NEW.contact_user_id AND s.version=(SELECT MAX(version) FROM accounting.service_plan_versions WHERE book_id=NEW.book_id))
 OR NEW.period IS DISTINCT FROM b.period
 OR NEW.version IS DISTINCT FROM (SELECT COALESCE(MAX(version),0)+1 FROM accounting.monthly_delivery_packages WHERE book_id=NEW.book_id)
 THEN RAISE EXCEPTION 'Exact published report locked period and consecutive version required' USING ERRCODE='23514'; END IF;
 payload:=NEW.snapshot_json::jsonb;
 IF payload->'manifest'->>'package_id' IS DISTINCT FROM NEW.id
 OR payload->'manifest'->>'book_id' IS DISTINCT FROM NEW.book_id
 OR payload->'manifest'->>'company_id' IS DISTINCT FROM NEW.company_id
 OR payload->'manifest'->>'period' IS DISTINCT FROM NEW.period
 OR payload->'manifest'->>'version' IS DISTINCT FROM NEW.version::TEXT
 OR payload->'manifest'->>'report_id' IS DISTINCT FROM NEW.report_id
 OR payload->'manifest'->>'report_version' IS DISTINCT FROM NEW.report_version::TEXT
 OR payload->'manifest'->>'contact_user_id' IS DISTINCT FROM NEW.contact_user_id
 OR payload->'manifest'->>'schema_version' NOT IN('1','2')
 OR jsonb_typeof(payload->'files') IS DISTINCT FROM 'object'
 OR jsonb_typeof(payload->'manifest'->'files') IS DISTINCT FROM 'array'
 OR (payload->>'manifest_json')::jsonb IS DISTINCT FROM payload->'manifest'
 OR encode(sha256(convert_to(payload->>'manifest_json','UTF8')),'hex') IS DISTINCT FROM NEW.manifest_sha256
 OR (payload->'files'->>'published_report.json')::jsonb->'content' IS DISTINCT FROM (SELECT content_json::jsonb FROM accounting.report_runs WHERE id=NEW.report_id)
 THEN RAISE EXCEPTION 'Frozen snapshot scope differs from package' USING ERRCODE='23514'; END IF;
 schema_version:=payload->'manifest'->>'schema_version';
 expected_files:=CASE WHEN schema_version='2' THEN 12 ELSE 11 END;
 IF schema_version='2' THEN
 IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(payload->'files') key)
 IS DISTINCT FROM ARRAY['README.html','account_balances.csv','bank_verification.json','formal_books.json','formal_vouchers.json','frozen_exports.json','materials.json','originals_index.json','published_report.json','report.html','tax_filings.json','unresolved_matters.json']
 OR jsonb_array_length(payload->'manifest'->'files')<>expected_files
 OR (SELECT COUNT(DISTINCT value->>'path') FROM jsonb_array_elements(payload->'manifest'->'files'))<>expected_files
 OR jsonb_typeof(payload->'files'->'tax_filings.json') IS DISTINCT FROM 'string'
 THEN RAISE EXCEPTION 'Exact monthly package file set required' USING ERRCODE='23514'; END IF;
 ELSE
 IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(payload->'files') key)
 IS DISTINCT FROM ARRAY['README.html','account_balances.csv','bank_verification.json','formal_books.json','formal_vouchers.json','frozen_exports.json','materials.json','originals_index.json','published_report.json','report.html','unresolved_matters.json']
 OR jsonb_array_length(payload->'manifest'->'files')<>expected_files
 OR (SELECT COUNT(DISTINCT value->>'path') FROM jsonb_array_elements(payload->'manifest'->'files'))<>expected_files
 OR payload->'files' ? 'tax_filings.json'
 THEN RAISE EXCEPTION 'Exact monthly package file set required' USING ERRCODE='23514'; END IF;
 END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(payload->'manifest'->'files') LOOP
 file_text:=payload->'files'->>(entry->>'path');
 IF file_text IS NULL OR jsonb_typeof(payload->'files'->(entry->>'path'))<>'string'
 OR entry->>'sha256' IS DISTINCT FROM encode(sha256(convert_to(file_text,'UTF8')),'hex')
 OR entry->>'size_bytes' IS DISTINCT FROM octet_length(file_text)::TEXT
 THEN RAISE EXCEPTION 'Monthly package file hash or size differs' USING ERRCODE='23514'; END IF;
 END LOOP;
 END IF;
 RETURN NEW;
 END $$;
