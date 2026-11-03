-- compatibility: incremental
-- Monthly package schema v2 adds the tax filing evidence snapshot. Historical
-- v1 packages remain valid and are still exactly 11 files.
DROP TRIGGER monthly_delivery_package_guard;
CREATE TRIGGER monthly_delivery_package_guard BEFORE INSERT ON monthly_delivery_packages BEGIN
 SELECT CASE WHEN NEW.created_by IS NOT accounting_actor_user() OR NEW.firm_id IS NOT accounting_actor_firm()
 OR NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=NEW.created_by AND can_review)
 OR NOT EXISTS(SELECT 1 FROM monthly_delivery_eligible_contacts WHERE book_id=NEW.book_id AND user_id=NEW.contact_user_id)
 THEN RAISE(ABORT,'Current reviewer and eligible client contact required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM report_runs r JOIN books b ON b.id=r.book_id
 JOIN internal_period_locks l ON l.book_id=b.id AND l.period=b.period
 JOIN service_plan_versions p ON p.book_id=b.id AND p.version=NEW.service_plan_version AND p.contact_user_id=NEW.contact_user_id
 WHERE r.id=NEW.report_id AND r.book_id=NEW.book_id AND r.version=NEW.report_version AND r.period=NEW.period
 AND r.status='published' AND r.reviewed_by IS NOT NULL AND r.reviewed_by<>r.created_by AND r.published_at IS NOT NULL
 AND b.period=NEW.period AND p.version=(SELECT MAX(version) FROM service_plan_versions WHERE book_id=b.id))
 OR NEW.version<>(SELECT COALESCE(MAX(version),0)+1 FROM monthly_delivery_packages WHERE book_id=NEW.book_id)
 THEN RAISE(ABORT,'Exact published report locked period and consecutive package version required') END;
 SELECT CASE WHEN json_extract(NEW.snapshot_json,'$.manifest.package_id') IS NOT NEW.id
 OR json_extract(NEW.snapshot_json,'$.manifest.book_id') IS NOT NEW.book_id
 OR json_extract(NEW.snapshot_json,'$.manifest.company_id') IS NOT NEW.company_id
 OR json_extract(NEW.snapshot_json,'$.manifest.period') IS NOT NEW.period
 OR json_extract(NEW.snapshot_json,'$.manifest.version') IS NOT NEW.version
 OR json_extract(NEW.snapshot_json,'$.manifest.report_id') IS NOT NEW.report_id
 OR json_extract(NEW.snapshot_json,'$.manifest.report_version') IS NOT NEW.report_version
 OR json_extract(NEW.snapshot_json,'$.manifest.contact_user_id') IS NOT NEW.contact_user_id
 OR json_extract(NEW.snapshot_json,'$.manifest.schema_version') NOT IN(1,2)
 OR json_type(NEW.snapshot_json,'$.files') IS NOT 'object'
 OR json_type(NEW.snapshot_json,'$.manifest.files') IS NOT 'array'
 OR json_extract(NEW.snapshot_json,'$.manifest_json') IS NULL
 OR json_extract(json_extract(NEW.snapshot_json,'$.manifest_json'),'$.package_id') IS NOT NEW.id
 OR json_array_length(NEW.snapshot_json,'$.manifest.files')<>
   CASE WHEN json_extract(NEW.snapshot_json,'$.manifest.schema_version')=2 THEN 12 ELSE 11 END
 OR (SELECT COUNT(*) FROM json_each(NEW.snapshot_json,'$.files'))<>
   CASE WHEN json_extract(NEW.snapshot_json,'$.manifest.schema_version')=2 THEN 12 ELSE 11 END
 OR (SELECT COUNT(DISTINCT json_extract(value,'$.path')) FROM json_each(NEW.snapshot_json,'$.manifest.files'))<>
   CASE WHEN json_extract(NEW.snapshot_json,'$.manifest.schema_version')=2 THEN 12 ELSE 11 END
 OR EXISTS(SELECT 1 FROM json_each(NEW.snapshot_json,'$.files')
   WHERE key NOT IN('README.html','report.html','account_balances.csv','formal_vouchers.json','formal_books.json','published_report.json','bank_verification.json','frozen_exports.json','materials.json','unresolved_matters.json','originals_index.json','tax_filings.json') OR type<>'text')
 OR (json_extract(NEW.snapshot_json,'$.manifest.schema_version')=1 AND json_type(NEW.snapshot_json,'$.files."tax_filings.json"') IS NOT NULL)
 OR (json_extract(NEW.snapshot_json,'$.manifest.schema_version')=2 AND json_type(NEW.snapshot_json,'$.files."tax_filings.json"') IS NOT 'text')
 THEN RAISE(ABORT,'Frozen snapshot scope differs from package') END;
 SELECT CASE WHEN json_extract(NEW.snapshot_json,'$.manifest_json') IS NOT json_extract(NEW.snapshot_json,'$.manifest')
 OR monthly_delivery_sha256(json_extract(NEW.snapshot_json,'$.manifest_json')) IS NOT NEW.manifest_sha256
 OR EXISTS(SELECT 1 FROM json_each(NEW.snapshot_json,'$.manifest.files') m
 LEFT JOIN json_each(NEW.snapshot_json,'$.files') f ON f.key=json_extract(m.value,'$.path')
 WHERE f.key IS NULL OR monthly_delivery_sha256(f.value) IS NOT json_extract(m.value,'$.sha256')
 OR length(CAST(f.value AS BLOB)) IS NOT json_extract(m.value,'$.size_bytes'))
 THEN RAISE(ABORT,'Monthly package manifest or file hash differs') END;
END;
