-- compatibility: incremental
-- Batch 10-2.1 费用报销与声明式工作流绑定（SQLite 侧）。
-- 与 PG 329 同构：企业级业务单据在移交账套前允许没有 book_id，并锁定单据版本。
PRAGMA defer_foreign_keys=ON;
CREATE TABLE IF NOT EXISTS workflow_instances_n01(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  book_id TEXT REFERENCES books(id),
  definition_id TEXT NOT NULL REFERENCES workflow_definitions(id),
  definition_version INTEGER NOT NULL CHECK(definition_version>0),
  process_kind TEXT NOT NULL CHECK(process_kind IN
    ('expense_reimbursement','payment_approval','fee_variance','monthly_close')),
  subject_type TEXT NOT NULL CHECK(length(trim(subject_type))>0),
  subject_id TEXT NOT NULL CHECK(length(trim(subject_id))>0),
  subject_version INTEGER,
  amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),
  current_node TEXT NOT NULL,
  path_json TEXT NOT NULL CHECK(json_valid(path_json)),
  status TEXT NOT NULL CHECK(status IN
    ('running','suspended','completed','rejected','escalated')),
  suspended_reason TEXT NOT NULL DEFAULT '',
  terminal_external_locked INTEGER NOT NULL DEFAULT 0 CHECK(terminal_external_locked IN (0,1)),
  irreversible_reached INTEGER NOT NULL DEFAULT 0 CHECK(irreversible_reached IN (0,1)),
  approvals_json TEXT NOT NULL CHECK(json_valid(approvals_json)),
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(book_id IS NOT NULL OR subject_type='business_event'),
  CHECK(NOT (subject_type='business_event' AND (subject_version IS NULL OR subject_version<=0))),
  CHECK(NOT (subject_type<>'business_event' AND subject_version IS NOT NULL))
);
INSERT INTO workflow_instances_n01(
  id,firm_id,company_id,book_id,definition_id,definition_version,process_kind,subject_type,
  subject_id,subject_version,amount_cents,current_node,path_json,status,suspended_reason,
  terminal_external_locked,irreversible_reached,approvals_json,created_by,created_at,updated_at)
SELECT id,firm_id,company_id,book_id,definition_id,definition_version,process_kind,subject_type,
  subject_id,NULL,amount_cents,current_node,path_json,status,suspended_reason,
  terminal_external_locked,irreversible_reached,approvals_json,created_by,created_at,updated_at
FROM workflow_instances;
DROP TABLE workflow_instances;
ALTER TABLE workflow_instances_n01 RENAME TO workflow_instances;

CREATE INDEX IF NOT EXISTS idx_workflow_instance_queue
  ON workflow_instances(firm_id,company_id,status,process_kind,updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_instance_business_subject
  ON workflow_instances(firm_id,company_id,subject_type,subject_id,subject_version)
  WHERE subject_type='business_event';

CREATE TRIGGER IF NOT EXISTS workflow_instances_no_delete
 BEFORE DELETE ON workflow_instances
BEGIN SELECT RAISE(ABORT,'workflow instances are retained for audit'); END;
CREATE TRIGGER IF NOT EXISTS workflow_instances_snapshot_locked
 BEFORE UPDATE ON workflow_instances
 WHEN NEW.definition_id IS NOT OLD.definition_id
   OR NEW.definition_version IS NOT OLD.definition_version
   OR NEW.process_kind IS NOT OLD.process_kind
   OR NEW.subject_type IS NOT OLD.subject_type
   OR NEW.subject_id IS NOT OLD.subject_id
   OR NEW.subject_version IS NOT OLD.subject_version
   OR NEW.amount_cents IS NOT OLD.amount_cents
   OR NEW.path_json IS NOT OLD.path_json
   OR NEW.firm_id IS NOT OLD.firm_id
   OR NEW.company_id IS NOT OLD.company_id
   OR NEW.book_id IS NOT OLD.book_id
   OR NEW.created_by IS NOT OLD.created_by
   OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'workflow instance start snapshot is locked'); END;
