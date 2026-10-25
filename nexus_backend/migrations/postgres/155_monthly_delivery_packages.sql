-- compatibility: incremental
CREATE TABLE accounting.monthly_delivery_packages(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 period TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),report_id TEXT NOT NULL REFERENCES accounting.report_runs(id),report_version INTEGER NOT NULL CHECK(report_version>0),
 contact_user_id TEXT NOT NULL REFERENCES accounting.users(id),service_plan_version INTEGER NOT NULL,
 manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256~'^[0-9a-f]{64}$'),
 snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='object' AND octet_length(snapshot_json)<=8000000),
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 100),request_fingerprint TEXT NOT NULL CHECK(request_fingerprint~'^[0-9a-f]{64}$'),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(book_id,version),UNIQUE(book_id,created_by,idempotency_key),UNIQUE(id,book_id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(book_id,service_plan_version) REFERENCES accounting.service_plan_versions(book_id,version)
);
CREATE TABLE accounting.monthly_delivery_acknowledgements(
 package_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256~'^[0-9a-f]{64}$'),reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(package_id,book_id,firm_id,company_id) REFERENCES accounting.monthly_delivery_packages(id,book_id,firm_id,company_id)
);
-- Bounded boolean only: callers cannot enumerate unrelated user grants.
CREATE FUNCTION accounting.monthly_delivery_contact_eligible(target_book TEXT,target_user TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book) AND EXISTS(
 SELECT 1 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.service_plan_versions p ON p.book_id=b.id AND p.version=(SELECT MAX(version) FROM accounting.service_plan_versions WHERE book_id=b.id)
 JOIN accounting.users u ON u.id=p.contact_user_id AND u.active=1
 JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE b.id=target_book AND b.firm_id=accounting.actor_firm() AND u.id=target_user
 AND (m.role IN('owner','admin') OR (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance'))))) $$;
CREATE FUNCTION accounting.monthly_delivery_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE b RECORD; payload JSONB; p RECORD; entry JSONB; file_text TEXT;
 BEGIN
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR b.company_id IS DISTINCT FROM NEW.company_id
 OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NEW.created_by IS DISTINCT FROM accounting.actor_user()
 OR NOT accounting.book_allowed(NEW.book_id) THEN
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
 OR jsonb_typeof(payload->'files') IS DISTINCT FROM 'object'
 OR jsonb_typeof(payload->'manifest'->'files') IS DISTINCT FROM 'array'
 OR (payload->>'manifest_json')::jsonb IS DISTINCT FROM payload->'manifest'
 OR encode(sha256(convert_to(payload->>'manifest_json','UTF8')),'hex') IS DISTINCT FROM NEW.manifest_sha256
 OR (payload->'files'->>'published_report.json')::jsonb->'content' IS DISTINCT FROM (SELECT content_json::jsonb FROM accounting.report_runs WHERE id=NEW.report_id)
 THEN RAISE EXCEPTION 'Frozen snapshot scope differs from package' USING ERRCODE='23514'; END IF;
 IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(payload->'files') key)
 IS DISTINCT FROM ARRAY['README.html','account_balances.csv','bank_verification.json','formal_books.json','formal_vouchers.json','frozen_exports.json','materials.json','originals_index.json','published_report.json','report.html','unresolved_matters.json']
 OR jsonb_array_length(payload->'manifest'->'files')<>11
 OR (SELECT COUNT(DISTINCT value->>'path') FROM jsonb_array_elements(payload->'manifest'->'files'))<>11
 THEN RAISE EXCEPTION 'Exact monthly package file set required' USING ERRCODE='23514'; END IF;
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
REVOKE ALL ON FUNCTION accounting.monthly_delivery_contact_eligible(TEXT,TEXT),accounting.monthly_delivery_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.monthly_delivery_contact_eligible(TEXT,TEXT) TO accounting_app;
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['monthly_delivery_packages','monthly_delivery_acknowledgements'] LOOP
 EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
 EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection',item);
 EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',item);
 EXECUTE format('CREATE POLICY monthly_delivery_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id))',item);
 IF item='monthly_delivery_packages' THEN
 EXECUTE format('CREATE POLICY monthly_delivery_insert ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.book_review_allowed(book_id))',item);
 ELSE
 EXECUTE format('CREATE POLICY monthly_delivery_insert ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.monthly_delivery_contact_eligible(book_id,created_by))',item);
 END IF;
 EXECUTE format('CREATE TRIGGER monthly_delivery_insert_guard BEFORE INSERT ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.monthly_delivery_guard()',item);
 EXECUTE format('CREATE TRIGGER monthly_delivery_company_scope BEFORE INSERT ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard()',item);
 EXECUTE format('CREATE TRIGGER monthly_delivery_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable()',item);
 END LOOP;
END $$;
