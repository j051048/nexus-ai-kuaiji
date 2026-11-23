-- compatibility: incremental
-- Batch 09-1.2 PLT08 助理回答留痕与三类受控记忆：回答快照（含依赖对象与版本）、
-- 个人偏好、企业规则提议。回答与规则都是「读时复核」的对象：权限撤销或依据版本
-- 变化后必须立即失效，因此这里保存依赖清单而不是缓存结论文本。
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.assistant_answers(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  period TEXT NOT NULL CHECK(length(btrim(period))>0),
  kind TEXT NOT NULL CHECK(kind IN
    ('why_not_delivered','revenue_mismatch','number_origin')),
  question_json JSONB NOT NULL,
  sections_json JSONB NOT NULL,
  dependencies_json JSONB NOT NULL,
  answer_fingerprint TEXT NOT NULL CHECK(length(answer_fingerprint)=64),
  conclusion_state TEXT NOT NULL CHECK(conclusion_state IN
    ('confirmed','unconfirmed','abstained')),
  status TEXT NOT NULL CHECK(status IN ('active','invalid')),
  invalid_reason TEXT NOT NULL DEFAULT '',
  cost_state TEXT NOT NULL CHECK(cost_state IN ('not_incurred','incurred','unknown')),
  superseded_by TEXT REFERENCES accounting.assistant_answers(id),
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_answer_scope
  ON accounting.assistant_answers(firm_id,company_id,book_id,period,kind,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_answer_fingerprint
  ON accounting.assistant_answers(firm_id,book_id,answer_fingerprint);

CREATE TABLE IF NOT EXISTS accounting.assistant_preferences(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  user_id TEXT NOT NULL REFERENCES accounting.users(id),
  pref_key TEXT NOT NULL CHECK(pref_key IN ('queue_order','default_period','panel_density')),
  pref_value TEXT NOT NULL CHECK(length(btrim(pref_value))>0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(firm_id,company_id,user_id,pref_key)
);

CREATE TABLE IF NOT EXISTS accounting.assistant_rule_proposals(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  rule_key TEXT NOT NULL CHECK(length(btrim(rule_key))>0),
  title TEXT NOT NULL CHECK(length(btrim(title))>0),
  payload_json JSONB NOT NULL,
  version INTEGER NOT NULL CHECK(version>0),
  status TEXT NOT NULL CHECK(status IN
    ('proposed','approved','rejected','retired','expired')),
  proposed_by TEXT NOT NULL REFERENCES accounting.users(id),
  approved_by TEXT REFERENCES accounting.users(id),
  effective_from TEXT,
  expires_at TEXT,
  state_version INTEGER NOT NULL DEFAULT 1 CHECK(state_version>0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(firm_id,company_id,rule_key,version)
);
-- 同一企业同一规则同时只能有一个生效版本：换版必须先撤销旧版，避免双版本叠加。
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_rule_single_approved
  ON accounting.assistant_rule_proposals(firm_id,company_id,rule_key)
  WHERE status='approved';
CREATE INDEX IF NOT EXISTS idx_assistant_rule_company
  ON accounting.assistant_rule_proposals(firm_id,company_id,status,rule_key);

ALTER TABLE accounting.assistant_answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_answers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assistant_answer_read ON accounting.assistant_answers;
CREATE POLICY assistant_answer_read ON accounting.assistant_answers
  FOR SELECT TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id)
  );
DROP POLICY IF EXISTS assistant_answer_insert ON accounting.assistant_answers;
CREATE POLICY assistant_answer_insert ON accounting.assistant_answers
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id)
    AND created_by = accounting.actor_user()
  );
DROP POLICY IF EXISTS assistant_answer_update ON accounting.assistant_answers;
CREATE POLICY assistant_answer_update ON accounting.assistant_answers
  FOR UPDATE TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id)
  )
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id)
  );

ALTER TABLE accounting.assistant_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_preferences FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assistant_preference_own ON accounting.assistant_preferences;
CREATE POLICY assistant_preference_own ON accounting.assistant_preferences
  FOR ALL TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND user_id = accounting.actor_user()
    AND accounting.company_visible(company_id)
  )
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND user_id = accounting.actor_user()
    AND accounting.company_visible(company_id)
  );

ALTER TABLE accounting.assistant_rule_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_rule_proposals FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assistant_rule_read ON accounting.assistant_rule_proposals;
CREATE POLICY assistant_rule_read ON accounting.assistant_rule_proposals
  FOR SELECT TO accounting_app
  USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
DROP POLICY IF EXISTS assistant_rule_insert ON accounting.assistant_rule_proposals;
CREATE POLICY assistant_rule_insert ON accounting.assistant_rule_proposals
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
    AND proposed_by = accounting.actor_user()
  );
DROP POLICY IF EXISTS assistant_rule_update ON accounting.assistant_rule_proposals;
CREATE POLICY assistant_rule_update ON accounting.assistant_rule_proposals
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

REVOKE ALL ON accounting.assistant_answers
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON accounting.assistant_preferences
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON accounting.assistant_rule_proposals
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT, UPDATE ON accounting.assistant_answers TO accounting_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON accounting.assistant_preferences TO accounting_app;
GRANT SELECT, INSERT, UPDATE ON accounting.assistant_rule_proposals TO accounting_app;

-- 回答是留痕：结论段与依赖清单一经写入不可改写，失效只能标记状态并指向新回答。
CREATE OR REPLACE FUNCTION accounting.assistant_answer_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'assistant answers are retained for audit' USING ERRCODE = '55000';
  END IF;
  IF NEW.sections_json IS DISTINCT FROM OLD.sections_json
    OR NEW.dependencies_json IS DISTINCT FROM OLD.dependencies_json
    OR NEW.answer_fingerprint IS DISTINCT FROM OLD.answer_fingerprint
    OR NEW.question_json IS DISTINCT FROM OLD.question_json
    OR NEW.conclusion_state IS DISTINCT FROM OLD.conclusion_state
    OR NEW.kind IS DISTINCT FROM OLD.kind
    OR NEW.firm_id IS DISTINCT FROM OLD.firm_id
    OR NEW.company_id IS DISTINCT FROM OLD.company_id
    OR NEW.book_id IS DISTINCT FROM OLD.book_id
    OR NEW.period IS DISTINCT FROM OLD.period
    OR NEW.created_by IS DISTINCT FROM OLD.created_by
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION 'assistant answer snapshots are immutable' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION accounting.assistant_answer_guard()
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS assistant_answer_no_change ON accounting.assistant_answers;
CREATE TRIGGER assistant_answer_no_change
  BEFORE UPDATE OR DELETE ON accounting.assistant_answers
  FOR EACH ROW EXECUTE FUNCTION accounting.assistant_answer_guard();

-- 企业规则：内容与版本号冻结，状态只能前进；删除一律拒绝，撤销用状态表达。
CREATE OR REPLACE FUNCTION accounting.assistant_rule_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'assistant rule proposals are retained for audit' USING ERRCODE = '55000';
  END IF;
  IF NEW.payload_json IS DISTINCT FROM OLD.payload_json
    OR NEW.rule_key IS DISTINCT FROM OLD.rule_key
    OR NEW.version IS DISTINCT FROM OLD.version
    OR NEW.proposed_by IS DISTINCT FROM OLD.proposed_by
    OR NEW.firm_id IS DISTINCT FROM OLD.firm_id
    OR NEW.company_id IS DISTINCT FROM OLD.company_id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION 'assistant rule content is frozen per version' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION accounting.assistant_rule_guard()
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS assistant_rule_no_change ON accounting.assistant_rule_proposals;
CREATE TRIGGER assistant_rule_no_change
  BEFORE UPDATE OR DELETE ON accounting.assistant_rule_proposals
  FOR EACH ROW EXECUTE FUNCTION accounting.assistant_rule_guard();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.assistant_answers;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.assistant_answers
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.assistant_preferences;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.assistant_preferences
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.assistant_rule_proposals;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.assistant_rule_proposals
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
