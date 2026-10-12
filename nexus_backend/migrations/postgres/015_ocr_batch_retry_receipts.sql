-- Record acceptance of a batch command, without storing the original visible
-- item set/count. Repeating a key cannot newly retry items that failed later.
CREATE TABLE accounting.ocr_batch_retries (
 batch_id TEXT NOT NULL,request_key TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 requested_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,requested_by,request_key),
 FOREIGN KEY(batch_id,firm_id,company_id) REFERENCES accounting.ocr_batches(id,firm_id,company_id)
);
CREATE TRIGGER ocr_batch_retries_immutable BEFORE UPDATE OR DELETE ON accounting.ocr_batch_retries
 FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
ALTER TABLE accounting.ocr_batch_retries ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ocr_batch_retries FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT ON accounting.ocr_batch_retries TO accounting_app;
CREATE POLICY ocr_batch_retries_read ON accounting.ocr_batch_retries FOR SELECT TO accounting_app
 USING(requested_by=accounting.actor_user() AND accounting.ocr_batch_visible(batch_id));
CREATE POLICY ocr_batch_retries_create ON accounting.ocr_batch_retries FOR INSERT TO accounting_app
 WITH CHECK(requested_by=accounting.actor_user() AND firm_id=accounting.actor_firm()
  AND accounting.actor_active() AND accounting.ocr_batch_visible(batch_id));
