-- Historical evidence is an isolated archive, never current sources/drafts.
CREATE TABLE migration_projects(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,
 external_book_id TEXT NOT NULL CHECK(length(external_book_id) BETWEEN 1 AND 200),source_system_key TEXT NOT NULL CHECK(length(source_system_key) BETWEEN 1 AND 100),
 source_product TEXT NOT NULL,product_version TEXT NOT NULL DEFAULT '',history_start TEXT NOT NULL,history_end TEXT NOT NULL,cutover_date TEXT NOT NULL,
 currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 status TEXT NOT NULL DEFAULT 'collecting' CHECK(status IN ('collecting','processing','needs_attention','ready','accepted','active','cancelled')),
 active_validation_id TEXT,create_key TEXT NOT NULL,create_fingerprint TEXT NOT NULL CHECK(length(create_fingerprint)=64),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,firm_id,company_id),UNIQUE(firm_id,created_by,create_key),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 CHECK(coalesce(length(history_start)=10 AND date(history_start,'+0 days')=history_start AND length(history_end)=10 AND date(history_end,'+0 days')=history_end AND length(cutover_date)=10 AND date(cutover_date,'+0 days')=cutover_date AND history_start<=history_end AND date(history_end,'+1 day')=cutover_date AND substr(history_start,9,2)='01' AND substr(cutover_date,9,2)='01',0))
);
CREATE TABLE migration_project_access(
 project_id TEXT NOT NULL REFERENCES migration_projects(id),user_id TEXT NOT NULL REFERENCES users(id),role TEXT NOT NULL CHECK(role IN ('viewer','reviewer','manager')),
 granted_by TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_at TEXT NOT NULL,PRIMARY KEY(project_id,user_id)
);
CREATE TABLE migration_files(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('package','accounts','auxiliaries','vouchers','balances','open_items','attachment')),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),storage_key TEXT NOT NULL UNIQUE CHECK(length(storage_key)=35 AND substr(storage_key,3,1)='/' AND substr(storage_key,1,2) NOT GLOB '*[^0-9a-f]*' AND substr(storage_key,4) NOT GLOB '*[^0-9a-f]*'),sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 52428800),media_type TEXT NOT NULL,mapping_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(mapping_json) AND json_type(mapping_json)='object'),
 create_key TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(project_id,create_key),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES migration_projects(id,firm_id,company_id)
);
CREATE TABLE migration_mapping_versions(
 project_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(project_id,version),UNIQUE(project_id,request_key),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES migration_projects(id,firm_id,company_id)
);
CREATE TABLE migration_jobs(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,file_id TEXT,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('parse','validate')),status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','retry_wait','succeeded','failed','stale','cancelled')),
 execution_user_id TEXT NOT NULL REFERENCES users(id),expected_version INTEGER NOT NULL CHECK(expected_version>0),attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10 AND attempt_count<=max_attempts),available_at TEXT NOT NULL,
 lease_token TEXT,lease_until TEXT,worker_id TEXT,error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 checkpoint_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(checkpoint_json) AND json_type(checkpoint_json)='object'),processed_count INTEGER NOT NULL DEFAULT 0 CHECK(processed_count>=0),record_count INTEGER NOT NULL DEFAULT 0 CHECK(record_count>=0),
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(project_id,request_key),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES migration_projects(id,firm_id,company_id),FOREIGN KEY(file_id,project_id,firm_id,company_id) REFERENCES migration_files(id,project_id,firm_id,company_id),
 CHECK((phase='parse' AND file_id IS NOT NULL) OR (phase='validate' AND file_id IS NULL)),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL) OR (status IN ('queued','retry_wait') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL) OR (status IN ('succeeded','failed','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX idx_migration_active_job ON migration_jobs(project_id,coalesce(file_id,''),phase) WHERE status IN ('queued','running','retry_wait');
CREATE INDEX idx_migration_dispatch ON migration_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE TABLE migration_records(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,file_id TEXT NOT NULL,job_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('account','auxiliary','voucher','balance','open_item')),external_id TEXT NOT NULL CHECK(length(external_id) BETWEEN 1 AND 200),period TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),row_number INTEGER NOT NULL CHECK(row_number>0),created_at TEXT NOT NULL,
 UNIQUE(job_id,kind,external_id,period),UNIQUE(job_id,file_id,row_number,kind,external_id),
 FOREIGN KEY(file_id,project_id,firm_id,company_id) REFERENCES migration_files(id,project_id,firm_id,company_id),FOREIGN KEY(job_id,project_id,firm_id,company_id) REFERENCES migration_jobs(id,project_id,firm_id,company_id)
);
CREATE INDEX idx_migration_records_page ON migration_records(project_id,kind,period,external_id,id);
CREATE TABLE migration_validations(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,job_id TEXT NOT NULL UNIQUE,input_version INTEGER NOT NULL CHECK(input_version>0),mapping_version INTEGER NOT NULL CHECK(mapping_version>=0),
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,basis_token TEXT NOT NULL,report_json TEXT NOT NULL CHECK(json_valid(report_json) AND json_type(report_json)='object'),
 blocking_count INTEGER NOT NULL CHECK(blocking_count>=0),warning_count INTEGER NOT NULL CHECK(warning_count>=0),created_at TEXT NOT NULL,
 CHECK(coalesce(json_type(report_json,'$.blocking_count')='integer' AND json_extract(report_json,'$.blocking_count')=blocking_count AND json_type(report_json,'$.warning_count')='integer' AND json_extract(report_json,'$.warning_count')=warning_count AND json_type(report_json,'$.issues')='array' AND json_type(report_json,'$.coverage')='object',0)),
 UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES migration_projects(id,firm_id,company_id),FOREIGN KEY(job_id,project_id,firm_id,company_id) REFERENCES migration_jobs(id,project_id,firm_id,company_id)
);
CREATE TABLE migration_acceptances(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,validation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 expected_version INTEGER NOT NULL CHECK(expected_version>0),basis_token TEXT NOT NULL,request_key TEXT NOT NULL,
 accepted_by TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(validation_id,project_id,firm_id,company_id) REFERENCES migration_validations(id,project_id,firm_id,company_id)
);
CREATE TABLE migration_cutovers(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL UNIQUE,validation_id TEXT NOT NULL,acceptance_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),request_key TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),FOREIGN KEY(validation_id,project_id,firm_id,company_id) REFERENCES migration_validations(id,project_id,firm_id,company_id),FOREIGN KEY(acceptance_id,project_id,firm_id,company_id) REFERENCES migration_acceptances(id,project_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE migration_company_schedule(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_dispatch_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_dispatch_seq>=0),PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id));
CREATE INDEX idx_migration_projects_scope ON migration_projects(firm_id,company_id,status,updated_at DESC,id);
CREATE INDEX idx_migration_files_project ON migration_files(project_id,created_at,id);
CREATE INDEX idx_migration_jobs_project ON migration_jobs(project_id,created_at DESC,id);
CREATE TABLE migration_file_replacements(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,old_file_id TEXT NOT NULL UNIQUE,new_file_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 request_key TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),CHECK(old_file_id<>new_file_id),FOREIGN KEY(old_file_id,project_id,firm_id,company_id) REFERENCES migration_files(id,project_id,firm_id,company_id),FOREIGN KEY(new_file_id,project_id,firm_id,company_id) REFERENCES migration_files(id,project_id,firm_id,company_id)
);
CREATE TRIGGER migration_project_identity BEFORE UPDATE ON migration_projects BEGIN
 SELECT CASE WHEN (NEW.id,NEW.firm_id,NEW.company_id,NEW.ledger_id,NEW.book_id,NEW.external_book_id,NEW.source_system_key,NEW.source_product,NEW.product_version,NEW.history_start,NEW.history_end,NEW.cutover_date,NEW.currency,NEW.created_by,NEW.created_at,NEW.create_key,NEW.create_fingerprint) IS NOT (OLD.id,OLD.firm_id,OLD.company_id,OLD.ledger_id,OLD.book_id,OLD.external_book_id,OLD.source_system_key,OLD.source_product,OLD.product_version,OLD.history_start,OLD.history_end,OLD.cutover_date,OLD.currency,OLD.created_by,OLD.created_at,OLD.create_key,OLD.create_fingerprint) OR NEW.version NOT IN (OLD.version,OLD.version+1) OR OLD.status IN ('active','cancelled') THEN RAISE(ABORT,'Historical migration identity is immutable') END;
END;
CREATE TRIGGER migration_record_scope BEFORE INSERT ON migration_records BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_jobs j JOIN migration_projects p ON p.id=j.project_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.file_id=NEW.file_id AND j.phase='parse' AND j.status='running' AND j.expected_version=p.version AND p.status NOT IN ('accepted','active','cancelled')) OR EXISTS(SELECT 1 FROM migration_file_replacements WHERE old_file_id=NEW.file_id) THEN RAISE(ABORT,'Historical record requires live parse generation') END;
END;
CREATE TRIGGER migration_validation_scope BEFORE INSERT ON migration_validations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_jobs j JOIN migration_projects p ON p.id=j.project_id WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.phase='validate' AND j.status='running' AND j.expected_version=NEW.input_version AND p.version=NEW.input_version AND p.status NOT IN ('accepted','active','cancelled')) OR NEW.mapping_version<>(SELECT coalesce(max(version),0) FROM migration_mapping_versions WHERE project_id=NEW.project_id) THEN RAISE(ABORT,'Historical validation requires current generation') END;
END;
CREATE TRIGGER migration_acceptance_guard BEFORE INSERT ON migration_acceptances BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_projects p JOIN migration_validations v ON v.id=NEW.validation_id JOIN migration_jobs j ON j.id=v.job_id JOIN users u ON u.id=NEW.accepted_by AND u.active=1 WHERE p.id=NEW.project_id AND p.status='ready' AND p.active_validation_id=v.id AND p.version=NEW.expected_version AND v.input_version=p.version AND v.basis_token=NEW.basis_token AND v.blocking_count=0 AND json_extract(v.report_json,'$.coverage.complete')=1 AND j.status='succeeded' AND NEW.accepted_by<>p.created_by AND NEW.accepted_by<>coalesce((SELECT created_by FROM migration_mapping_versions WHERE project_id=p.id ORDER BY version DESC LIMIT 1),p.created_by) AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.firm_id=p.firm_id AND fc.company_id=p.company_id AND fc.status='active')) THEN RAISE(ABORT,'Acceptance requires current successful independent validation') END;
END;
CREATE TRIGGER migration_cutover_guard BEFORE INSERT ON migration_cutovers BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_projects p JOIN migration_acceptances a ON a.id=NEW.acceptance_id JOIN migration_validations v ON v.id=NEW.validation_id JOIN periods q ON q.book_id=p.book_id JOIN users u ON u.id=NEW.created_by AND u.active=1 WHERE p.id=NEW.project_id AND p.status='accepted' AND p.book_id=NEW.book_id AND p.active_validation_id=v.id AND a.validation_id=v.id AND a.expected_version=p.version AND a.basis_token=v.basis_token AND v.blocking_count=0 AND q.status='open') OR EXISTS(SELECT 1 FROM sources WHERE book_id=NEW.book_id) OR EXISTS(SELECT 1 FROM drafts WHERE book_id=NEW.book_id) OR EXISTS(SELECT 1 FROM exports WHERE book_id=NEW.book_id) THEN RAISE(ABORT,'Cutover requires accepted validation and empty open target') END;
END;
CREATE TRIGGER migration_acceptance_publish AFTER INSERT ON migration_acceptances BEGIN UPDATE migration_projects SET status='accepted',updated_at=NEW.created_at WHERE id=NEW.project_id;END;
CREATE TRIGGER migration_cutover_publish AFTER INSERT ON migration_cutovers BEGIN UPDATE migration_projects SET status='active',updated_at=NEW.created_at WHERE id=NEW.project_id;END;
CREATE TRIGGER migration_files_no_update BEFORE UPDATE ON migration_files BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_files_no_delete BEFORE DELETE ON migration_files BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_mapping_versions_no_update BEFORE UPDATE ON migration_mapping_versions BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_mapping_versions_no_delete BEFORE DELETE ON migration_mapping_versions BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_records_no_update BEFORE UPDATE ON migration_records BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_records_no_delete BEFORE DELETE ON migration_records BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_validations_no_update BEFORE UPDATE ON migration_validations BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_validations_no_delete BEFORE DELETE ON migration_validations BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_acceptances_no_update BEFORE UPDATE ON migration_acceptances BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_acceptances_no_delete BEFORE DELETE ON migration_acceptances BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_cutovers_no_update BEFORE UPDATE ON migration_cutovers BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_cutovers_no_delete BEFORE DELETE ON migration_cutovers BEGIN SELECT RAISE(ABORT,'Historical migration evidence is immutable');END;
CREATE TRIGGER migration_projects_no_delete BEFORE DELETE ON migration_projects BEGIN SELECT RAISE(ABORT,'Historical migration identity is immutable');END;
CREATE UNIQUE INDEX idx_migration_cutover_one_target ON migration_cutovers(book_id);
CREATE TABLE migration_external_keys(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL,source_system_key TEXT NOT NULL,external_book_id TEXT NOT NULL,
 kind TEXT NOT NULL,period TEXT NOT NULL,external_id TEXT NOT NULL,project_id TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES migration_records(id),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),created_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,ledger_id,source_system_key,external_book_id,kind,period,external_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES migration_projects(id,firm_id,company_id),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id)
);
CREATE TRIGGER migration_file_replacement_scope BEFORE INSERT ON migration_file_replacements BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_projects p JOIN migration_files o ON o.project_id=p.id JOIN migration_files n ON n.project_id=p.id AND n.category=o.category WHERE p.id=NEW.project_id AND p.status NOT IN ('accepted','active','cancelled') AND o.id=NEW.old_file_id AND n.id=NEW.new_file_id) OR EXISTS(SELECT 1 FROM migration_file_replacements WHERE old_file_id=NEW.old_file_id OR new_file_id=NEW.new_file_id OR old_file_id=NEW.new_file_id) THEN RAISE(ABORT,'Original correction requires current same-category files') END;
END;
CREATE TRIGGER migration_replacements_no_update BEFORE UPDATE ON migration_file_replacements BEGIN SELECT RAISE(ABORT,'Historical correction evidence is immutable');END;
CREATE TRIGGER migration_replacements_no_delete BEFORE DELETE ON migration_file_replacements BEGIN SELECT RAISE(ABORT,'Historical correction evidence is immutable');END;
CREATE TRIGGER migration_external_keys_no_update BEFORE UPDATE ON migration_external_keys BEGIN SELECT RAISE(ABORT,'External history identity is immutable');END;
CREATE TRIGGER migration_external_keys_no_delete BEFORE DELETE ON migration_external_keys BEGIN SELECT RAISE(ABORT,'External history identity is immutable');END;
CREATE TRIGGER migration_register_keys AFTER INSERT ON migration_cutovers BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM migration_projects p JOIN migration_records r ON r.project_id=p.id JOIN migration_jobs q ON q.id=r.job_id JOIN migration_external_keys k ON k.firm_id=p.firm_id AND k.ledger_id=p.ledger_id AND k.source_system_key=p.source_system_key AND k.external_book_id=p.external_book_id AND k.kind=r.kind AND k.period=r.period AND k.external_id=r.external_id WHERE p.id=NEW.project_id AND q.status='succeeded' AND q.phase='parse' AND NOT EXISTS(SELECT 1 FROM migration_file_replacements x WHERE x.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM migration_jobs n WHERE n.project_id=p.id AND n.file_id=q.file_id AND n.phase='parse' AND n.status='succeeded' AND (n.created_at,n.id)>(q.created_at,q.id)) AND k.fingerprint<>r.fingerprint) THEN RAISE(ABORT,'Previously activated external history conflicts') END;
 INSERT OR IGNORE INTO migration_external_keys(firm_id,company_id,ledger_id,source_system_key,external_book_id,kind,period,external_id,project_id,record_id,fingerprint,created_at)
 SELECT p.firm_id,p.company_id,p.ledger_id,p.source_system_key,p.external_book_id,r.kind,r.period,r.external_id,p.id,r.id,r.fingerprint,NEW.created_at FROM migration_projects p JOIN migration_records r ON r.project_id=p.id JOIN migration_jobs q ON q.id=r.job_id WHERE p.id=NEW.project_id AND q.phase='parse' AND q.status='succeeded' AND NOT EXISTS(SELECT 1 FROM migration_file_replacements x WHERE x.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM migration_jobs n WHERE n.project_id=p.id AND n.file_id=q.file_id AND n.phase='parse' AND n.status='succeeded' AND (n.created_at,n.id)>(q.created_at,q.id));
END;
CREATE TRIGGER migration_project_create_scope BEFORE INSERT ON migration_projects BEGIN
 SELECT CASE WHEN NEW.status<>'collecting' OR NEW.version<>1 OR NEW.active_validation_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM ledger_periods l JOIN books b ON b.id=l.book_id JOIN periods q ON q.book_id=b.id AND q.period=b.period WHERE l.ledger_id=NEW.ledger_id AND l.book_id=NEW.book_id AND l.firm_id=NEW.firm_id AND l.company_id=NEW.company_id AND q.status='open' AND substr(NEW.cutover_date,1,7)=b.period) OR EXISTS(SELECT 1 FROM migration_projects p WHERE p.firm_id=NEW.firm_id AND p.source_system_key=NEW.source_system_key AND p.external_book_id=NEW.external_book_id AND (p.company_id<>NEW.company_id OR p.ledger_id<>NEW.ledger_id)) THEN RAISE(ABORT,'Migration requires stable source identity and open mapped target') END;
END;
