CREATE TABLE accounting.quality_benchmarks(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 100),version INTEGER NOT NULL CHECK(version>0),
 standard_ids_json TEXT NOT NULL CHECK(jsonb_typeof(standard_ids_json::jsonb)='array' AND jsonb_array_length(standard_ids_json::jsonb) BETWEEN 1 AND 100),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 UNIQUE(book_id,name,version),UNIQUE(firm_id,created_by,request_key),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE INDEX idx_quality_benchmark_book ON accounting.quality_benchmarks(book_id,created_at DESC,id);
ALTER TABLE accounting.quality_benchmarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.quality_benchmarks FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT ON accounting.quality_benchmarks TO accounting_app;
CREATE POLICY quality_benchmark_read ON accounting.quality_benchmarks FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.catalog_manage(book_id));
CREATE POLICY quality_benchmark_create ON accounting.quality_benchmarks FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
CREATE FUNCTION accounting.quality_benchmark_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.standard_ids_json::jsonb) x WHERE jsonb_typeof(x.value)<>'string')
 OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(NEW.standard_ids_json::jsonb))
 OR (SELECT count(*)<>count(DISTINCT s.sample_id) FROM jsonb_array_elements_text(NEW.standard_ids_json::jsonb) x JOIN accounting.quality_standards s ON s.id=x.value)
 OR EXISTS(
  SELECT 1 FROM jsonb_array_elements_text(NEW.standard_ids_json::jsonb) x WHERE NOT EXISTS(SELECT 1 FROM accounting.quality_standards s JOIN accounting.quality_confirmations c ON c.standard_id=s.id AND c.book_id=s.book_id WHERE s.id=x.value AND s.book_id=NEW.book_id AND s.firm_id=NEW.firm_id AND accounting.quality_standard_current(s.id))) THEN
  RAISE EXCEPTION 'Sample sets require current independently confirmed standards' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER quality_benchmark_scope BEFORE INSERT ON accounting.quality_benchmarks FOR EACH ROW EXECUTE FUNCTION accounting.quality_benchmark_guard();
CREATE TRIGGER quality_benchmark_immutable BEFORE UPDATE OR DELETE ON accounting.quality_benchmarks FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
REVOKE ALL ON FUNCTION accounting.quality_benchmark_guard() FROM PUBLIC;
