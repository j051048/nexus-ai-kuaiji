-- compatibility: incremental
-- DCP04：逐项取消/跳过需要留下原因与意图，重试次数不能被 attempt 覆盖。
ALTER TABLE batch_operation_items ADD COLUMN cancel_requested_at TEXT;
ALTER TABLE batch_operation_items ADD COLUMN cancel_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE batch_operation_items ADD COLUMN skip_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE batch_operation_items ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE batch_operations ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0;
