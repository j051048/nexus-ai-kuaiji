-- compatibility: incremental
-- Batch 09-1.1 声明式工作流编排与版本管理中台：版本化 DAG 定义、锁定版本的实例、
-- 只追加的步骤日志。历史已发布版本永不改写：发版只追加新版本号，回滚同样是
-- 「生成新版本 + 归档旧激活版」，因此这里用状态机而非原地编辑来表达演进。
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.workflow_definitions(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  process_kind TEXT NOT NULL CHECK(process_kind IN
    ('expense_reimbursement','payment_approval','fee_variance','monthly_close')),
  version INTEGER NOT NULL CHECK(version>0),
  status TEXT NOT NULL CHECK(status IN ('draft','active','archived')),
  nodes_json JSONB NOT NULL,
  routing_json JSONB NOT NULL,
  content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),
  note TEXT NOT NULL DEFAULT '',
  rollback_from_version INTEGER,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TEXT NOT NULL,
  published_by TEXT REFERENCES accounting.users(id),
  published_at TEXT,
  UNIQUE(firm_id,company_id,process_kind,version)
);
-- 同一流程同一法人同一时点只允许一条激活版本；历史版本靠版本号排序保留。
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_definition_active
  ON accounting.workflow_definitions(firm_id,company_id,process_kind)
  WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_workflow_definition_versions
  ON accounting.workflow_definitions(firm_id,company_id,process_kind,version DESC);

CREATE TABLE IF NOT EXISTS accounting.workflow_instances(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  definition_id TEXT NOT NULL REFERENCES accounting.workflow_definitions(id),
  definition_version INTEGER NOT NULL CHECK(definition_version>0),
  process_kind TEXT NOT NULL CHECK(process_kind IN
    ('expense_reimbursement','payment_approval','fee_variance','monthly_close')),
  subject_type TEXT NOT NULL CHECK(length(btrim(subject_type))>0),
  subject_id TEXT NOT NULL CHECK(length(btrim(subject_id))>0),
  amount_cents BIGINT NOT NULL CHECK(amount_cents>=0),
  current_node TEXT NOT NULL,
  path_json JSONB NOT NULL,
  status TEXT NOT NULL CHECK(status IN
    ('running','suspended','completed','rejected','escalated')),
  suspended_reason TEXT NOT NULL DEFAULT '',
  terminal_external_locked BOOLEAN NOT NULL DEFAULT FALSE,
  irreversible_reached BOOLEAN NOT NULL DEFAULT FALSE,
  approvals_json JSONB NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_instance_queue
  ON accounting.workflow_instances(firm_id,company_id,status,process_kind,updated_at);

CREATE TABLE IF NOT EXISTS accounting.workflow_step_logs(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  instance_id TEXT NOT NULL REFERENCES accounting.workflow_instances(id),
  node_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN
    ('start','approve','reject','suspend','resume','rollback','escalate_timeout','auto_approve',
     'external_submit')),
  from_node TEXT NOT NULL DEFAULT '',
  to_node TEXT NOT NULL DEFAULT '',
  actor_user_id TEXT REFERENCES accounting.users(id),
  reason TEXT NOT NULL DEFAULT '',
  metadata_json JSONB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_step_log_instance
  ON accounting.workflow_step_logs(firm_id,instance_id,created_at,id);

ALTER TABLE accounting.workflow_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.workflow_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_definition_read ON accounting.workflow_definitions;
CREATE POLICY workflow_definition_read ON accounting.workflow_definitions
  FOR SELECT TO accounting_app
  USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
DROP POLICY IF EXISTS workflow_definition_insert ON accounting.workflow_definitions;
CREATE POLICY workflow_definition_insert ON accounting.workflow_definitions
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
    AND created_by = accounting.actor_user()
  );
DROP POLICY IF EXISTS workflow_definition_update ON accounting.workflow_definitions;
CREATE POLICY workflow_definition_update ON accounting.workflow_definitions
  FOR UPDATE TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
  )
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
  );

ALTER TABLE accounting.workflow_instances ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.workflow_instances FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_instance_read ON accounting.workflow_instances;
CREATE POLICY workflow_instance_read ON accounting.workflow_instances
  FOR SELECT TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id)
  );
DROP POLICY IF EXISTS workflow_instance_insert ON accounting.workflow_instances;
CREATE POLICY workflow_instance_insert ON accounting.workflow_instances
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id)
    AND created_by = accounting.actor_user()
  );
DROP POLICY IF EXISTS workflow_instance_update ON accounting.workflow_instances;
CREATE POLICY workflow_instance_update ON accounting.workflow_instances
  FOR UPDATE TO accounting_app
  USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id))
  WITH CHECK (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id));

ALTER TABLE accounting.workflow_step_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.workflow_step_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_step_log_read ON accounting.workflow_step_logs;
CREATE POLICY workflow_step_log_read ON accounting.workflow_step_logs
  FOR SELECT TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND EXISTS(SELECT 1 FROM accounting.workflow_instances i
      WHERE i.id = instance_id AND i.firm_id = workflow_step_logs.firm_id)
  );
DROP POLICY IF EXISTS workflow_step_log_insert ON accounting.workflow_step_logs;
CREATE POLICY workflow_step_log_insert ON accounting.workflow_step_logs
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (actor_user_id IS NULL OR actor_user_id = accounting.actor_user())
    AND EXISTS(SELECT 1 FROM accounting.workflow_instances i
      WHERE i.id = instance_id AND i.firm_id = workflow_step_logs.firm_id)
  );

REVOKE ALL ON accounting.workflow_definitions
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON accounting.workflow_instances
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON accounting.workflow_step_logs
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT, UPDATE ON accounting.workflow_definitions TO accounting_app;
GRANT SELECT, INSERT, UPDATE ON accounting.workflow_instances TO accounting_app;
GRANT SELECT, INSERT ON accounting.workflow_step_logs TO accounting_app;

-- 已发布版本（active/archived）的规则、阈值与指纹不可篡改；只允许 draft→已发布
-- 的状态流转与激活版归档。删除一律拒绝：版本历史必须连续可查。
CREATE OR REPLACE FUNCTION accounting.workflow_definition_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'workflow definitions are versioned and cannot be deleted'
      USING ERRCODE = '55000';
  END IF;
  IF OLD.status IN ('active','archived') AND (
    NEW.nodes_json IS DISTINCT FROM OLD.nodes_json
    OR NEW.routing_json IS DISTINCT FROM OLD.routing_json
    OR NEW.content_fingerprint IS DISTINCT FROM OLD.content_fingerprint
    OR NEW.version IS DISTINCT FROM OLD.version
    OR NEW.process_kind IS DISTINCT FROM OLD.process_kind
    OR NEW.firm_id IS DISTINCT FROM OLD.firm_id
    OR NEW.company_id IS DISTINCT FROM OLD.company_id
    OR NEW.created_by IS DISTINCT FROM OLD.created_by
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  ) THEN
    RAISE EXCEPTION 'published workflow versions are immutable' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION accounting.workflow_definition_guard()
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS workflow_definition_no_change ON accounting.workflow_definitions;
CREATE TRIGGER workflow_definition_no_change
  BEFORE UPDATE OR DELETE ON accounting.workflow_definitions
  FOR EACH ROW EXECUTE FUNCTION accounting.workflow_definition_guard();

-- 实例启动时锁定的定义、版本、主体与金额构成「正在走的这条路」，不允许事后改写。
CREATE OR REPLACE FUNCTION accounting.workflow_instance_lock_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'workflow instances are retained for audit' USING ERRCODE = '55000';
  END IF;
  IF NEW.definition_id IS DISTINCT FROM OLD.definition_id
    OR NEW.definition_version IS DISTINCT FROM OLD.definition_version
    OR NEW.process_kind IS DISTINCT FROM OLD.process_kind
    OR NEW.subject_type IS DISTINCT FROM OLD.subject_type
    OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
    OR NEW.amount_cents IS DISTINCT FROM OLD.amount_cents
    OR NEW.path_json IS DISTINCT FROM OLD.path_json
    OR NEW.firm_id IS DISTINCT FROM OLD.firm_id
    OR NEW.company_id IS DISTINCT FROM OLD.company_id
    OR NEW.book_id IS DISTINCT FROM OLD.book_id
    OR NEW.created_by IS DISTINCT FROM OLD.created_by
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION 'workflow instance start snapshot is locked' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION accounting.workflow_instance_lock_guard()
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS workflow_instance_snapshot_lock ON accounting.workflow_instances;
CREATE TRIGGER workflow_instance_snapshot_lock
  BEFORE UPDATE OR DELETE ON accounting.workflow_instances
  FOR EACH ROW EXECUTE FUNCTION accounting.workflow_instance_lock_guard();

CREATE OR REPLACE FUNCTION accounting.workflow_step_log_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'workflow step logs are append-only' USING ERRCODE = '55000';
END;
$$;
REVOKE ALL ON FUNCTION accounting.workflow_step_log_append_only()
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS workflow_step_logs_no_change ON accounting.workflow_step_logs;
CREATE TRIGGER workflow_step_logs_no_change
  BEFORE UPDATE OR DELETE ON accounting.workflow_step_logs
  FOR EACH ROW EXECUTE FUNCTION accounting.workflow_step_log_append_only();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.workflow_definitions;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.workflow_definitions
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.workflow_instances;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.workflow_instances
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.workflow_step_logs;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.workflow_step_logs
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
