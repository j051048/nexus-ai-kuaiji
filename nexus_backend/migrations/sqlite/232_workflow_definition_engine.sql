-- compatibility: incremental
-- Batch 09-1.1 声明式工作流编排与版本管理中台（SQLite 侧）。
-- 与 PG 侧 325 同构：版本化 DAG 定义、锁定启动快照的实例、只追加步骤日志。
CREATE TABLE IF NOT EXISTS workflow_definitions(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  process_kind TEXT NOT NULL CHECK(process_kind IN
    ('expense_reimbursement','payment_approval','fee_variance','monthly_close')),
  version INTEGER NOT NULL CHECK(version>0),
  status TEXT NOT NULL CHECK(status IN ('draft','active','archived')),
  nodes_json TEXT NOT NULL CHECK(json_valid(nodes_json)),
  routing_json TEXT NOT NULL CHECK(json_valid(routing_json)),
  content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),
  note TEXT NOT NULL DEFAULT '',
  rollback_from_version INTEGER,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  published_by TEXT REFERENCES users(id),
  published_at TEXT,
  UNIQUE(firm_id,company_id,process_kind,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_definition_active
  ON workflow_definitions(firm_id,company_id,process_kind)
  WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_workflow_definition_versions
  ON workflow_definitions(firm_id,company_id,process_kind,version DESC);

CREATE TABLE IF NOT EXISTS workflow_instances(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  definition_id TEXT NOT NULL REFERENCES workflow_definitions(id),
  definition_version INTEGER NOT NULL CHECK(definition_version>0),
  process_kind TEXT NOT NULL CHECK(process_kind IN
    ('expense_reimbursement','payment_approval','fee_variance','monthly_close')),
  subject_type TEXT NOT NULL CHECK(length(trim(subject_type))>0),
  subject_id TEXT NOT NULL CHECK(length(trim(subject_id))>0),
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
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_instance_queue
  ON workflow_instances(firm_id,company_id,status,process_kind,updated_at);

CREATE TABLE IF NOT EXISTS workflow_step_logs(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  instance_id TEXT NOT NULL REFERENCES workflow_instances(id),
  node_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN
    ('start','approve','reject','suspend','resume','rollback','escalate_timeout','auto_approve',
     'external_submit')),
  from_node TEXT NOT NULL DEFAULT '',
  to_node TEXT NOT NULL DEFAULT '',
  actor_user_id TEXT REFERENCES users(id),
  reason TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_step_log_instance
  ON workflow_step_logs(firm_id,instance_id,created_at,id);

CREATE TRIGGER IF NOT EXISTS workflow_definitions_no_delete
 BEFORE DELETE ON workflow_definitions
BEGIN SELECT RAISE(ABORT,'workflow definitions are versioned and cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS workflow_definitions_published_frozen
 BEFORE UPDATE ON workflow_definitions
 WHEN OLD.status IN ('active','archived') AND (
   NEW.nodes_json IS NOT OLD.nodes_json
   OR NEW.routing_json IS NOT OLD.routing_json
   OR NEW.content_fingerprint IS NOT OLD.content_fingerprint
   OR NEW.version IS NOT OLD.version
   OR NEW.process_kind IS NOT OLD.process_kind
   OR NEW.firm_id IS NOT OLD.firm_id
   OR NEW.company_id IS NOT OLD.company_id
   OR NEW.created_by IS NOT OLD.created_by
   OR NEW.created_at IS NOT OLD.created_at)
BEGIN SELECT RAISE(ABORT,'published workflow versions are immutable'); END;

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
   OR NEW.amount_cents IS NOT OLD.amount_cents
   OR NEW.path_json IS NOT OLD.path_json
   OR NEW.firm_id IS NOT OLD.firm_id
   OR NEW.company_id IS NOT OLD.company_id
   OR NEW.book_id IS NOT OLD.book_id
   OR NEW.created_by IS NOT OLD.created_by
   OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'workflow instance start snapshot is locked'); END;

CREATE TRIGGER IF NOT EXISTS workflow_step_logs_no_update
 BEFORE UPDATE ON workflow_step_logs
BEGIN SELECT RAISE(ABORT,'workflow step logs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS workflow_step_logs_no_delete
 BEFORE DELETE ON workflow_step_logs
BEGIN SELECT RAISE(ABORT,'workflow step logs are append-only'); END;
