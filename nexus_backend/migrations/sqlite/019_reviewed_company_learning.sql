-- Reviewed lineage is immutable. Rules never become effective by correction.
CREATE TABLE learning_samples (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),period TEXT NOT NULL,
 draft_id TEXT NOT NULL UNIQUE,source_id TEXT NOT NULL,reviewed_version INTEGER NOT NULL CHECK(reviewed_version>0),
 approved_version INTEGER NOT NULL CHECK(approved_version=reviewed_version+1),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),original_json TEXT NOT NULL CHECK(json_valid(original_json)),
 final_json TEXT NOT NULL CHECK(json_valid(final_json)),corrections_json TEXT NOT NULL CHECK(json_valid(corrections_json)),
 evidence_fingerprint TEXT NOT NULL CHECK(length(evidence_fingerprint)=64),sample_fingerprint TEXT NOT NULL CHECK(length(sample_fingerprint)=64),
 corrected INTEGER NOT NULL CHECK(corrected IN (0,1)),reviewed_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id),FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),UNIQUE(id,book_id)
);
CREATE INDEX idx_learning_samples_book ON learning_samples(book_id,created_at DESC,id DESC);
CREATE TABLE learning_rules (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),period TEXT NOT NULL,
 current_version INTEGER NOT NULL CHECK(current_version>0),state_version INTEGER NOT NULL CHECK(state_version>0),
 status TEXT NOT NULL CHECK(status IN ('draft','published','retired')),evaluation_id TEXT,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),UNIQUE(id,book_id)
);
CREATE INDEX idx_learning_rules_book ON learning_rules(book_id,status);
CREATE TABLE learning_rule_versions (
 rule_id TEXT NOT NULL,book_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),definition_fingerprint TEXT NOT NULL CHECK(length(definition_fingerprint)=64),
 sample_ids_json TEXT NOT NULL CHECK(json_valid(sample_ids_json) AND json_array_length(sample_ids_json)>0),reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(rule_id,version),
 FOREIGN KEY(rule_id,book_id) REFERENCES learning_rules(id,book_id)
);
CREATE TABLE learning_rule_evaluations (
 id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,book_id TEXT NOT NULL,rule_version INTEGER NOT NULL,
 negative_sample_ids_json TEXT NOT NULL CHECK(json_valid(negative_sample_ids_json) AND json_array_length(negative_sample_ids_json)>0),
 input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),report_json TEXT NOT NULL CHECK(json_valid(report_json)),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(rule_id,book_id) REFERENCES learning_rules(id,book_id),FOREIGN KEY(rule_id,rule_version) REFERENCES learning_rule_versions(rule_id,version)
);
CREATE TRIGGER learning_samples_review_guard BEFORE INSERT ON learning_samples BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts d JOIN books b ON b.id=d.book_id WHERE d.id=NEW.draft_id AND d.book_id=NEW.book_id AND d.source_id=NEW.source_id AND d.status='approved' AND d.version=NEW.approved_version AND d.approved_by=NEW.reviewed_by AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND b.period=NEW.period)
 THEN RAISE(ABORT,'learning sample requires approved source lineage') END; END;
CREATE TRIGGER learning_samples_immutable_update BEFORE UPDATE ON learning_samples BEGIN SELECT RAISE(ABORT,'learning sample history is immutable'); END;
CREATE TRIGGER learning_samples_immutable_delete BEFORE DELETE ON learning_samples BEGIN SELECT RAISE(ABORT,'learning sample history is immutable'); END;
CREATE TRIGGER learning_versions_immutable_update BEFORE UPDATE ON learning_rule_versions BEGIN SELECT RAISE(ABORT,'learning rule history is immutable'); END;
CREATE TRIGGER learning_versions_immutable_delete BEFORE DELETE ON learning_rule_versions BEGIN SELECT RAISE(ABORT,'learning rule history is immutable'); END;
CREATE TRIGGER learning_evaluations_immutable_update BEFORE UPDATE ON learning_rule_evaluations BEGIN SELECT RAISE(ABORT,'learning evaluation history is immutable'); END;
CREATE TRIGGER learning_evaluations_immutable_delete BEFORE DELETE ON learning_rule_evaluations BEGIN SELECT RAISE(ABORT,'learning evaluation history is immutable'); END;
CREATE TRIGGER learning_rule_publish_guard BEFORE UPDATE ON learning_rules WHEN NEW.status='published' BEGIN
 SELECT CASE WHEN OLD.status<>'draft' OR NEW.current_version<>OLD.current_version OR NEW.state_version<>OLD.state_version+1 OR NOT EXISTS(SELECT 1 FROM learning_rule_evaluations e WHERE e.id=NEW.evaluation_id AND e.rule_id=NEW.id AND e.book_id=NEW.book_id AND e.rule_version=NEW.current_version AND json_extract(e.report_json,'$.passed')=1)
 THEN RAISE(ABORT,'learning publication requires passing current evaluation') END; END;
