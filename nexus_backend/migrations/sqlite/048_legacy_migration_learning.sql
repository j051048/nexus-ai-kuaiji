-- Private historical examples and deliberately published current-book formulas.
CREATE TABLE migration_learning_candidates(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','reviewed','rejected','published','retired')),
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json) AND json_type(definition_json)='object'),positive_record_ids_json TEXT NOT NULL CHECK(json_valid(positive_record_ids_json) AND json_type(positive_record_ids_json)='array' AND json_array_length(positive_record_ids_json) BETWEEN 1 AND 100),
 eligibility_json TEXT NOT NULL CHECK(json_valid(eligibility_json) AND json_type(eligibility_json)='object'),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),UNIQUE(id,project_id,book_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES migration_projects(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE migration_learning_samples(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES migration_records(id),project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 polarity TEXT NOT NULL CHECK(polarity IN ('positive','negative')),snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json) AND json_type(snapshot_json)='object'),final_json TEXT NOT NULL CHECK(json_valid(final_json) AND json_type(final_json)='object'),
 quality_json TEXT NOT NULL CHECK(json_valid(quality_json) AND json_extract(quality_json,'$.source_type')='historical'),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,record_id),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE migration_learning_reviews(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,input_version INTEGER NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),evidence_confirmed INTEGER NOT NULL CHECK(evidence_confirmed=1),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),reviewed_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),UNIQUE(candidate_id,input_version),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE migration_learning_shadows(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,input_version INTEGER NOT NULL,
 negative_record_ids_json TEXT NOT NULL CHECK(json_valid(negative_record_ids_json) AND json_type(negative_record_ids_json)='array' AND json_array_length(negative_record_ids_json) BETWEEN 1 AND 100),
 context_fingerprint TEXT NOT NULL CHECK(length(context_fingerprint)=64),report_json TEXT NOT NULL CHECK(json_valid(report_json) AND json_type(report_json)='object'),passed INTEGER NOT NULL CHECK(passed IN (0,1) AND coalesce(json_extract(report_json,'$.passed')=passed,0)),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE migration_learning_rules(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL UNIQUE,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version=1),
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json) AND json_type(definition_json)='object'),shadow_id TEXT NOT NULL REFERENCES migration_learning_shadows(id),context_fingerprint TEXT NOT NULL CHECK(length(context_fingerprint)=64),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),published_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE migration_learning_retirements(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,rule_id TEXT NOT NULL UNIQUE REFERENCES migration_learning_rules(id),project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE INDEX idx_migration_learning_candidates_page ON migration_learning_candidates(project_id,book_id,created_at DESC,id);
CREATE INDEX idx_migration_learning_samples_candidate ON migration_learning_samples(candidate_id,polarity,record_id);
CREATE INDEX idx_migration_learning_rules_book ON migration_learning_rules(book_id,firm_id,company_id);
CREATE TRIGGER migration_learning_candidate_scope BEFORE INSERT ON migration_learning_candidates BEGIN
 SELECT CASE WHEN NEW.version<>1 OR NEW.status<>'candidate' OR NOT EXISTS(SELECT 1 FROM migration_projects p JOIN migration_cutovers c ON c.project_id=p.id WHERE p.id=NEW.project_id AND p.book_id=NEW.book_id AND p.status='active') THEN RAISE(ABORT,'Historical candidate requires active cutover') END;
END;
CREATE TRIGGER migration_learning_candidate_identity BEFORE UPDATE ON migration_learning_candidates BEGIN
 SELECT CASE WHEN (NEW.id,NEW.project_id,NEW.book_id,NEW.firm_id,NEW.company_id,NEW.definition_json,NEW.positive_record_ids_json,NEW.eligibility_json,NEW.reason,NEW.request_key,NEW.request_fingerprint,NEW.created_by,NEW.created_at) IS NOT (OLD.id,OLD.project_id,OLD.book_id,OLD.firm_id,OLD.company_id,OLD.definition_json,OLD.positive_record_ids_json,OLD.eligibility_json,OLD.reason,OLD.request_key,OLD.request_fingerprint,OLD.created_by,OLD.created_at) OR NEW.version<>OLD.version+1 OR NOT ((OLD.status='candidate' AND NEW.status IN ('reviewed','rejected')) OR (OLD.status='reviewed' AND NEW.status='published') OR (OLD.status='published' AND NEW.status='retired')) THEN RAISE(ABORT,'Historical candidate definition and status lineage are immutable') END;
END;
CREATE TRIGGER migration_learning_review_scope BEFORE INSERT ON migration_learning_reviews BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_learning_candidates c WHERE c.id=NEW.candidate_id AND c.status='candidate' AND c.version=NEW.input_version AND c.created_by<>NEW.reviewed_by) THEN RAISE(ABORT,'Historical example requires independent current review') END;
END;
CREATE TRIGGER migration_learning_review_publish AFTER INSERT ON migration_learning_reviews BEGIN UPDATE migration_learning_candidates SET status=CASE WHEN NEW.decision='approve' THEN 'reviewed' ELSE 'rejected' END,version=version+1,updated_at=NEW.created_at WHERE id=NEW.candidate_id;END;
CREATE TRIGGER migration_learning_rule_scope BEFORE INSERT ON migration_learning_rules BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_learning_candidates c JOIN migration_learning_shadows s ON s.candidate_id=c.id AND s.id=NEW.shadow_id JOIN periods p ON p.book_id=c.book_id WHERE c.id=NEW.candidate_id AND c.status='reviewed' AND c.version=s.input_version AND c.created_by<>NEW.published_by AND c.definition_json=NEW.definition_json AND s.passed=1 AND s.context_fingerprint=NEW.context_fingerprint AND p.status='open') THEN RAISE(ABORT,'Historical rule requires independent successful replay') END;
END;
CREATE TRIGGER migration_learning_rule_publish AFTER INSERT ON migration_learning_rules BEGIN UPDATE migration_learning_candidates SET status='published',version=version+1,updated_at=NEW.created_at WHERE id=NEW.candidate_id;END;
CREATE TRIGGER migration_learning_retirement_scope BEFORE INSERT ON migration_learning_retirements BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM migration_learning_rules r JOIN migration_learning_candidates c ON c.id=r.candidate_id WHERE r.id=NEW.rule_id AND c.id=NEW.candidate_id AND c.status='published') THEN RAISE(ABORT,'Only a published historical formula can retire') END;
END;
CREATE TRIGGER migration_learning_retirement_publish AFTER INSERT ON migration_learning_retirements BEGIN UPDATE migration_learning_candidates SET status='retired',version=version+1,updated_at=NEW.created_at WHERE id=NEW.candidate_id;END;
CREATE TRIGGER migration_learning_samples_no_update BEFORE UPDATE ON migration_learning_samples BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_samples_no_delete BEFORE DELETE ON migration_learning_samples BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_reviews_no_update BEFORE UPDATE ON migration_learning_reviews BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_reviews_no_delete BEFORE DELETE ON migration_learning_reviews BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_shadows_no_update BEFORE UPDATE ON migration_learning_shadows BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_shadows_no_delete BEFORE DELETE ON migration_learning_shadows BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_rules_no_update BEFORE UPDATE ON migration_learning_rules BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_rules_no_delete BEFORE DELETE ON migration_learning_rules BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_retirements_no_update BEFORE UPDATE ON migration_learning_retirements BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_retirements_no_delete BEFORE DELETE ON migration_learning_retirements BEGIN SELECT RAISE(ABORT,'Historical learning evidence is immutable');END;
CREATE TRIGGER migration_learning_candidates_no_delete BEFORE DELETE ON migration_learning_candidates BEGIN SELECT RAISE(ABORT,'Historical learning identity is immutable');END;
