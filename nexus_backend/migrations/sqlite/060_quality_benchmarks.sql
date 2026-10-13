CREATE TABLE quality_benchmarks(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 100),version INTEGER NOT NULL CHECK(version>0),
 standard_ids_json TEXT NOT NULL CHECK(json_valid(standard_ids_json) AND json_type(standard_ids_json)='array' AND json_array_length(standard_ids_json) BETWEEN 1 AND 100),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 UNIQUE(book_id,name,version),UNIQUE(firm_id,created_by,request_key),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE INDEX idx_quality_benchmark_book ON quality_benchmarks(book_id,created_at DESC,id);
CREATE TRIGGER quality_benchmark_scope BEFORE INSERT ON quality_benchmarks
 WHEN (SELECT count(*) FROM json_each(NEW.standard_ids_json))<>(SELECT count(DISTINCT value) FROM json_each(NEW.standard_ids_json))
 OR (SELECT count(*) FROM json_each(NEW.standard_ids_json) x JOIN quality_standards s ON s.id=x.value)<>(SELECT count(DISTINCT s.sample_id) FROM json_each(NEW.standard_ids_json) x JOIN quality_standards s ON s.id=x.value)
 OR EXISTS(SELECT 1 FROM json_each(NEW.standard_ids_json) x WHERE x.type<>'text' OR NOT EXISTS(
 SELECT 1 FROM quality_standards s JOIN quality_confirmations c ON c.standard_id=s.id AND c.book_id=s.book_id
 WHERE s.id=x.value AND s.book_id=NEW.book_id AND s.firm_id=NEW.firm_id AND s.company_id=NEW.company_id))
 BEGIN SELECT RAISE(ABORT,'Sample sets require independently confirmed standards in the same book'); END;
CREATE TRIGGER quality_benchmark_immutable_update BEFORE UPDATE ON quality_benchmarks BEGIN SELECT RAISE(ABORT,'Sample set versions are immutable'); END;
CREATE TRIGGER quality_benchmark_immutable_delete BEFORE DELETE ON quality_benchmarks BEGIN SELECT RAISE(ABORT,'Sample set versions are immutable'); END;
