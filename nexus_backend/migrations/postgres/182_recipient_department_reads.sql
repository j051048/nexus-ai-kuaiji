-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
CREATE OR REPLACE FUNCTION accounting.monthly_delivery_contact_eligible(target_book TEXT,target_user TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(
 SELECT 1 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.service_plan_versions p ON p.book_id=b.id AND p.version=(SELECT MAX(version) FROM accounting.service_plan_versions WHERE book_id=b.id)
 JOIN accounting.users u ON u.id=p.contact_user_id AND u.active=1
 JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE b.id=target_book AND b.firm_id=accounting.actor_firm() AND u.id=target_user
 AND (accounting.book_allowed(target_book) OR target_user=accounting.actor_user())
 AND (m.role IN('owner','admin') OR (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance')))
 OR EXISTS(SELECT 1 FROM accounting.delivery_contact_access g WHERE g.book_id=b.id AND g.user_id=u.id AND g.state='active' AND (g.valid_to IS NULL OR g.valid_to::timestamptz>statement_timestamp())))) $$;
CREATE OR REPLACE FUNCTION accounting.recipient_package_allowed(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.monthly_delivery_packages p WHERE p.id=target AND p.firm_id=accounting.actor_firm() AND p.contact_user_id=accounting.actor_user() AND p.created_by<>accounting.actor_user() AND accounting.monthly_delivery_contact_eligible(p.book_id,accounting.actor_user())) $$;
REVOKE ALL ON FUNCTION accounting.recipient_package_allowed(TEXT) FROM PUBLIC,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.recipient_package_allowed(TEXT) TO accounting_app;
DROP POLICY IF EXISTS recipient_package_read ON accounting.monthly_delivery_packages;
CREATE POLICY recipient_package_read ON accounting.monthly_delivery_packages FOR SELECT TO accounting_app USING(accounting.recipient_package_allowed(id));
DROP POLICY IF EXISTS recipient_ack_read ON accounting.monthly_delivery_acknowledgements;
CREATE POLICY recipient_ack_read ON accounting.monthly_delivery_acknowledgements FOR SELECT TO accounting_app USING(accounting.recipient_package_allowed(package_id));
DROP POLICY IF EXISTS recipient_open_read ON accounting.monthly_delivery_opens;
CREATE POLICY recipient_open_read ON accounting.monthly_delivery_opens FOR SELECT TO accounting_app USING(accounting.recipient_package_allowed(package_id));
CREATE OR REPLACE FUNCTION accounting.recipient_package_book(target TEXT,target_book TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT jsonb_build_object('id',b.id,'firm_id',b.firm_id,'company_id',b.company_id,'period',b.period,'name',b.name)::text FROM accounting.books b JOIN accounting.monthly_delivery_packages p ON p.book_id=b.id WHERE p.id=target AND b.id=target_book AND accounting.recipient_package_allowed(target) $$;
REVOKE ALL ON FUNCTION accounting.recipient_package_book(TEXT,TEXT) FROM PUBLIC,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.recipient_package_book(TEXT,TEXT) TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.recipient_package_index() RETURNS TABLE(id TEXT,book_id TEXT,company_id TEXT,period TEXT,version INTEGER,manifest_sha256 TEXT,company_name TEXT) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT p.id,p.book_id,p.company_id,p.period,p.version,p.manifest_sha256,c.name FROM accounting.monthly_delivery_packages p JOIN accounting.companies c ON c.id=p.company_id WHERE accounting.recipient_package_allowed(p.id) ORDER BY p.created_at DESC,p.id LIMIT 100 $$;
REVOKE ALL ON FUNCTION accounting.recipient_package_index() FROM PUBLIC,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.recipient_package_index() TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.department_expense_summary(target_company TEXT,target_department TEXT,target_period TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT jsonb_build_object('company_id',d.company_id,'department_id',d.id,'department_name',d.name,'period',target_period,'expense_cents',COALESCE((SELECT SUM(l.debit_cents-l.credit_cents) FROM accounting.voucher_lines l JOIN accounting.vouchers v ON v.id=l.voucher_id JOIN accounting.books b ON b.id=v.book_id WHERE b.firm_id=accounting.actor_firm() AND b.company_id=d.company_id AND v.period=target_period AND l.category_snapshot='expense' AND l.aux_json::jsonb->>'department'=d.id),0),'basis','正式凭证中明确归属本部门的费用净额；不含未分配费用，不代表企业全部费用')::text
 FROM accounting.departments d JOIN accounting.department_approvers a ON a.department_id=d.id AND a.company_id=d.company_id AND a.firm_id=accounting.actor_firm() AND a.user_id=accounting.actor_user() JOIN accounting.company_memberships cm ON cm.company_id=d.company_id AND cm.user_id=a.user_id
 JOIN accounting.firm_clients fc ON fc.firm_id=a.firm_id AND fc.company_id=d.company_id AND fc.status='active' JOIN accounting.companies c ON c.id=d.company_id AND c.status='active'
 WHERE d.id=target_department AND d.company_id=target_company AND accounting.actor_active() AND target_period ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' $$;
REVOKE ALL ON FUNCTION accounting.department_expense_summary(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.department_expense_summary(TEXT,TEXT,TEXT) TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.monthly_delivery_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE b RECORD; payload JSONB; p RECORD; entry JSONB; file_text TEXT;
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
