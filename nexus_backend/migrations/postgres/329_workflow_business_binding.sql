-- compatibility: incremental
-- Batch 10-2.1 费用报销与声明式工作流绑定（PostgreSQL 侧）。
-- 企业级业务单据（subject_type='business_event'）在移交账套前没有 book_id，
-- 因此放宽 book_id 可空，并把实例的读写策略拆成「账套内实例」与「企业级实例」两条路径。
SET LOCAL search_path=accounting,pg_catalog;

ALTER TABLE accounting.workflow_instances ALTER COLUMN book_id DROP NOT NULL;
ALTER TABLE accounting.workflow_instances ADD COLUMN IF NOT EXISTS subject_version INTEGER;
ALTER TABLE accounting.workflow_instances DROP CONSTRAINT IF EXISTS workflow_instance_book_scope;
ALTER TABLE accounting.workflow_instances
  ADD CONSTRAINT workflow_instance_book_scope CHECK (book_id IS NOT NULL OR subject_type = 'business_event');
ALTER TABLE accounting.workflow_instances DROP CONSTRAINT IF EXISTS workflow_instance_subject_version_scope;
ALTER TABLE accounting.workflow_instances
  ADD CONSTRAINT workflow_instance_subject_version_scope CHECK (
    (book_id IS NULL AND subject_type = 'business_event' AND subject_version IS NOT NULL AND subject_version > 0)
    OR (book_id IS NOT NULL AND (subject_version IS NULL OR (subject_type = 'business_event' AND subject_version > 0)))
  );

-- 同一张业务单据的同一个提交版本最多只生成一条实例；历史有账套但无 subject_version 的记录保留原义不误伤。
DROP INDEX IF EXISTS accounting.idx_workflow_instance_business_subject;
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_instance_business_subject
  ON accounting.workflow_instances(firm_id, company_id, subject_type, subject_id, subject_version)
  WHERE subject_type = 'business_event' AND subject_version IS NOT NULL;

DROP POLICY IF EXISTS workflow_instance_read ON accounting.workflow_instances;
CREATE POLICY workflow_instance_read ON accounting.workflow_instances
  FOR SELECT TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (
      (book_id IS NOT NULL AND accounting.book_allowed(book_id))
      OR (
        book_id IS NULL
        AND subject_type = 'business_event'
        AND (
          accounting.business_event_visible(subject_id, firm_id, company_id)
          OR accounting.firm_manager(firm_id)
          OR accounting.company_manager(company_id)
        )
      )
    )
  );
DROP POLICY IF EXISTS workflow_instance_insert ON accounting.workflow_instances;
CREATE POLICY workflow_instance_insert ON accounting.workflow_instances
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND created_by = accounting.actor_user()
    AND (
      (book_id IS NOT NULL AND accounting.book_allowed(book_id))
      OR (
        book_id IS NULL
        AND subject_type = 'business_event'
        AND accounting.business_event_visible(subject_id, firm_id, company_id)
      )
    )
  );
DROP POLICY IF EXISTS workflow_instance_update ON accounting.workflow_instances;
CREATE POLICY workflow_instance_update ON accounting.workflow_instances
  FOR UPDATE TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (
      (book_id IS NOT NULL AND accounting.book_allowed(book_id))
      OR (
        book_id IS NULL
        AND subject_type = 'business_event'
        AND (
          accounting.business_event_visible(subject_id, firm_id, company_id)
          OR accounting.firm_manager(firm_id)
          OR accounting.company_manager(company_id)
        )
      )
    )
  )
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (
      (book_id IS NOT NULL AND accounting.book_allowed(book_id))
      OR (
        book_id IS NULL
        AND subject_type = 'business_event'
        AND (
          accounting.business_event_visible(subject_id, firm_id, company_id)
          OR accounting.firm_manager(firm_id)
          OR accounting.company_manager(company_id)
        )
      )
    )
  );

CREATE OR REPLACE FUNCTION accounting.workflow_instance_subject_lock() RETURNS trigger
LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  IF NEW.subject_id IS DISTINCT FROM OLD.subject_id
     OR NEW.subject_version IS DISTINCT FROM OLD.subject_version THEN
    RAISE EXCEPTION 'workflow instance subject binding is locked' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION accounting.workflow_instance_subject_lock() FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS workflow_instance_subject_lock ON accounting.workflow_instances;
CREATE TRIGGER workflow_instance_subject_lock
  BEFORE UPDATE ON accounting.workflow_instances
  FOR EACH ROW EXECUTE FUNCTION accounting.workflow_instance_subject_lock();
