-- A durable command receipt stores no original item IDs or counts.
CREATE TABLE ocr_batch_retries (
 batch_id TEXT NOT NULL,request_key TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 requested_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,requested_by,request_key),
 FOREIGN KEY(batch_id,firm_id,company_id) REFERENCES ocr_batches(id,firm_id,company_id)
);
CREATE TRIGGER ocr_batch_retries_no_update BEFORE UPDATE ON ocr_batch_retries BEGIN SELECT RAISE(ABORT,'Queue batch retry receipt is immutable'); END;
CREATE TRIGGER ocr_batch_retries_no_delete BEFORE DELETE ON ocr_batch_retries BEGIN SELECT RAISE(ABORT,'Queue batch retry receipt is immutable'); END;
