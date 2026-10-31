-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
-- Reuse the standard book-scoped write gate; review inserts invalidate preflight.
DROP TRIGGER IF EXISTS p0_close_dependency_gate ON accounting.legacy_review_preparations;
CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.legacy_review_preparations FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
DROP TRIGGER IF EXISTS p0_close_dependency_gate ON accounting.legacy_review_confirmations;
CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.legacy_review_confirmations FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
