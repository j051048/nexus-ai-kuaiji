-- compatibility: incremental
-- Independent monthly client delivery; never a financial software import receipt.
CREATE TABLE monthly_delivery_packages(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 period TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 report_id TEXT NOT NULL REFERENCES report_runs(id),report_version INTEGER NOT NULL CHECK(report_version>0),
 contact_user_id TEXT NOT NULL REFERENCES users(id),service_plan_version INTEGER NOT NULL,
 manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^a-f0-9]*'),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json) AND length(CAST(snapshot_json AS BLOB))<=8000000),
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 100),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(book_id,version),UNIQUE(book_id,created_by,idempotency_key),UNIQUE(id,book_id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(book_id,service_plan_version) REFERENCES service_plan_versions(book_id,version)
);
CREATE TABLE monthly_delivery_acknowledgements(
 package_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^a-f0-9]*'),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(package_id,book_id,firm_id,company_id) REFERENCES monthly_delivery_packages(id,book_id,firm_id,company_id)
);
CREATE VIEW monthly_delivery_eligible_contacts AS
 SELECT b.id AS book_id,u.id AS user_id FROM books b
 JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN service_plan_versions p ON p.book_id=b.id AND p.version=(SELECT MAX(version) FROM service_plan_versions WHERE book_id=b.id)
 JOIN users u ON u.id=p.contact_user_id AND u.active=1
 JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE m.role IN('owner','admin') OR (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance')));
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
 OR json_type(NEW.snapshot_json,'$.files') IS NOT 'object'
 OR json_type(NEW.snapshot_json,'$.manifest.files') IS NOT 'array'
 OR json_extract(NEW.snapshot_json,'$.manifest_json') IS NULL
 OR json_extract(json_extract(NEW.snapshot_json,'$.manifest_json'),'$.package_id') IS NOT NEW.id
 OR json_array_length(NEW.snapshot_json,'$.manifest.files')<>11
 OR (SELECT COUNT(*) FROM json_each(NEW.snapshot_json,'$.files'))<>11
 OR (SELECT COUNT(DISTINCT json_extract(value,'$.path')) FROM json_each(NEW.snapshot_json,'$.manifest.files'))<>11
 OR EXISTS(SELECT 1 FROM json_each(NEW.snapshot_json,'$.files') WHERE key NOT IN('README.html','report.html','account_balances.csv','formal_vouchers.json','formal_books.json','published_report.json','bank_verification.json','frozen_exports.json','materials.json','unresolved_matters.json','originals_index.json') OR type<>'text')
 THEN RAISE(ABORT,'Frozen snapshot scope differs from package') END;
 SELECT CASE WHEN json_extract(NEW.snapshot_json,'$.manifest_json') IS NOT json_extract(NEW.snapshot_json,'$.manifest')
 OR monthly_delivery_sha256(json_extract(NEW.snapshot_json,'$.manifest_json')) IS NOT NEW.manifest_sha256
 OR EXISTS(SELECT 1 FROM json_each(NEW.snapshot_json,'$.manifest.files') m
 LEFT JOIN json_each(NEW.snapshot_json,'$.files') f ON f.key=json_extract(m.value,'$.path')
 WHERE f.key IS NULL OR monthly_delivery_sha256(f.value) IS NOT json_extract(m.value,'$.sha256')
 OR length(CAST(f.value AS BLOB)) IS NOT json_extract(m.value,'$.size_bytes'))
 THEN RAISE(ABORT,'Monthly package manifest or file hash differs') END;
END;
CREATE TRIGGER monthly_delivery_ack_guard BEFORE INSERT ON monthly_delivery_acknowledgements BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM monthly_delivery_packages p JOIN monthly_delivery_eligible_contacts c ON c.book_id=p.book_id AND c.user_id=p.contact_user_id
 WHERE p.id=NEW.package_id AND p.book_id=NEW.book_id AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id
 AND p.contact_user_id=NEW.created_by AND p.created_by<>NEW.created_by AND p.manifest_sha256=NEW.manifest_sha256
 AND NEW.created_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm())
 THEN RAISE(ABORT,'Exact package hash and current client contact required') END;
END;
CREATE TRIGGER monthly_delivery_packages_no_update BEFORE UPDATE ON monthly_delivery_packages BEGIN SELECT RAISE(ABORT,'Monthly package is append-only'); END;
CREATE TRIGGER monthly_delivery_packages_no_delete BEFORE DELETE ON monthly_delivery_packages BEGIN SELECT RAISE(ABORT,'Monthly package is append-only'); END;
CREATE TRIGGER monthly_delivery_ack_no_update BEFORE UPDATE ON monthly_delivery_acknowledgements BEGIN SELECT RAISE(ABORT,'Client acknowledgement is append-only'); END;
CREATE TRIGGER monthly_delivery_ack_no_delete BEFORE DELETE ON monthly_delivery_acknowledgements BEGIN SELECT RAISE(ABORT,'Client acknowledgement is append-only'); END;
