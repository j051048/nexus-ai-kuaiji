CREATE TABLE quality_standards (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,period TEXT NOT NULL,
 sample_id TEXT NOT NULL,draft_id TEXT NOT NULL,source_id TEXT NOT NULL,snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
 final_json TEXT NOT NULL CHECK(json_valid(final_json)),evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),labels_json TEXT NOT NULL CHECK(json_type(labels_json)='array' AND json_array_length(labels_json)<=100),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 UNIQUE(id,book_id),UNIQUE(firm_id,created_by,request_key),FOREIGN KEY(sample_id,book_id) REFERENCES learning_samples(id,book_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id),FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id)
);
CREATE INDEX idx_quality_standards_page ON quality_standards(book_id,created_at DESC,id DESC);
CREATE TABLE quality_confirmations (
 standard_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,confirmed_by TEXT NOT NULL REFERENCES users(id),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),evidence_checked INTEGER NOT NULL CHECK(evidence_checked=1),created_at TEXT NOT NULL,
 FOREIGN KEY(standard_id,book_id) REFERENCES quality_standards(id,book_id)
);
CREATE TABLE quality_runs (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,period TEXT NOT NULL,
 standard_ids_json TEXT NOT NULL CHECK(json_type(standard_ids_json)='array' AND json_array_length(standard_ids_json) BETWEEN 1 AND 100),
 settings_json TEXT NOT NULL CHECK(json_valid(settings_json)),input_fingerprint TEXT NOT NULL,report_json TEXT NOT NULL CHECK(json_valid(report_json)),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 UNIQUE(firm_id,created_by,request_key),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE INDEX idx_quality_runs_page ON quality_runs(book_id,created_at DESC,id DESC);
CREATE VIEW quality_standard_types AS SELECT id,book_id,firm_id,json_extract(snapshot_json,'$.business_type') AS business_type FROM quality_standards;
CREATE TRIGGER quality_standard_truth BEFORE INSERT ON quality_standards WHEN NOT EXISTS(
 SELECT 1 FROM learning_samples s JOIN drafts d ON d.id=s.draft_id AND d.book_id=s.book_id
 WHERE s.id=NEW.sample_id AND s.book_id=NEW.book_id AND s.firm_id=NEW.firm_id AND s.company_id=NEW.company_id AND s.period=NEW.period
 AND s.draft_id=NEW.draft_id AND s.source_id=NEW.source_id AND s.snapshot_json=NEW.snapshot_json AND s.final_json=NEW.final_json AND d.status IN ('approved','exported'))
 BEGIN SELECT RAISE(ABORT,'quality standard requires actual reviewed evidence');END;
CREATE TRIGGER quality_independent_confirmation BEFORE INSERT ON quality_confirmations WHEN NOT EXISTS(
 SELECT 1 FROM quality_standards s WHERE s.id=NEW.standard_id AND s.book_id=NEW.book_id AND s.firm_id=NEW.firm_id AND s.created_by<>NEW.confirmed_by)
 BEGIN SELECT RAISE(ABORT,'quality confirmation requires another reviewer');END;
CREATE TRIGGER quality_standards_no_update BEFORE UPDATE ON quality_standards BEGIN SELECT RAISE(ABORT,'quality history immutable');END;
CREATE TRIGGER quality_standards_no_delete BEFORE DELETE ON quality_standards BEGIN SELECT RAISE(ABORT,'quality history immutable');END;
CREATE TRIGGER quality_confirmations_no_update BEFORE UPDATE ON quality_confirmations BEGIN SELECT RAISE(ABORT,'quality history immutable');END;
CREATE TRIGGER quality_confirmations_no_delete BEFORE DELETE ON quality_confirmations BEGIN SELECT RAISE(ABORT,'quality history immutable');END;
CREATE TRIGGER quality_runs_no_update BEFORE UPDATE ON quality_runs BEGIN SELECT RAISE(ABORT,'quality history immutable');END;
CREATE TRIGGER quality_runs_no_delete BEFORE DELETE ON quality_runs BEGIN SELECT RAISE(ABORT,'quality history immutable');END;
