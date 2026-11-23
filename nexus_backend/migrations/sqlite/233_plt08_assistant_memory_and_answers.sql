-- compatibility: incremental
-- Batch 09-1.2 PLT08 助理回答留痕与三类受控记忆（SQLite 侧）。
-- 约束与 PostgreSQL 保持一致：回答快照与规则内容冻结、规则单一生效版本、
-- 个人偏好只属于本人。
CREATE TABLE IF NOT EXISTS assistant_answers(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  period TEXT NOT NULL CHECK(length(trim(period))>0),
  kind TEXT NOT NULL CHECK(kind IN
    ('why_not_delivered','revenue_mismatch','number_origin')),
  question_json TEXT NOT NULL CHECK(json_valid(question_json)),
  sections_json TEXT NOT NULL CHECK(json_valid(sections_json)),
  dependencies_json TEXT NOT NULL CHECK(json_valid(dependencies_json)),
  answer_fingerprint TEXT NOT NULL CHECK(length(answer_fingerprint)=64),
  conclusion_state TEXT NOT NULL CHECK(conclusion_state IN
    ('confirmed','unconfirmed','abstained')),
  status TEXT NOT NULL CHECK(status IN ('active','invalid')),
  invalid_reason TEXT NOT NULL DEFAULT '',
  cost_state TEXT NOT NULL CHECK(cost_state IN ('not_incurred','incurred','unknown')),
  superseded_by TEXT REFERENCES assistant_answers(id),
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_answer_scope
  ON assistant_answers(firm_id,company_id,book_id,period,kind,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_answer_fingerprint
  ON assistant_answers(firm_id,book_id,answer_fingerprint);

CREATE TABLE IF NOT EXISTS assistant_preferences(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  pref_key TEXT NOT NULL CHECK(pref_key IN ('queue_order','default_period','panel_density')),
  pref_value TEXT NOT NULL CHECK(length(trim(pref_value))>0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(firm_id,company_id,user_id,pref_key)
);

CREATE TABLE IF NOT EXISTS assistant_rule_proposals(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  rule_key TEXT NOT NULL CHECK(length(trim(rule_key))>0),
  title TEXT NOT NULL CHECK(length(trim(title))>0),
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
  version INTEGER NOT NULL CHECK(version>0),
  status TEXT NOT NULL CHECK(status IN
    ('proposed','approved','rejected','retired','expired')),
  proposed_by TEXT NOT NULL REFERENCES users(id),
  approved_by TEXT REFERENCES users(id),
  effective_from TEXT,
  expires_at TEXT,
  state_version INTEGER NOT NULL DEFAULT 1 CHECK(state_version>0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(firm_id,company_id,rule_key,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_rule_single_approved
  ON assistant_rule_proposals(firm_id,company_id,rule_key)
  WHERE status='approved';
CREATE INDEX IF NOT EXISTS idx_assistant_rule_company
  ON assistant_rule_proposals(firm_id,company_id,status,rule_key);

CREATE TRIGGER IF NOT EXISTS assistant_answers_no_delete
  BEFORE DELETE ON assistant_answers
  BEGIN
    SELECT RAISE(ABORT,'assistant answers are retained for audit');
  END;
CREATE TRIGGER IF NOT EXISTS assistant_answers_snapshot_frozen
  BEFORE UPDATE ON assistant_answers
  WHEN NEW.sections_json IS NOT OLD.sections_json
    OR NEW.dependencies_json IS NOT OLD.dependencies_json
    OR NEW.answer_fingerprint IS NOT OLD.answer_fingerprint
    OR NEW.question_json IS NOT OLD.question_json
    OR NEW.conclusion_state IS NOT OLD.conclusion_state
    OR NEW.kind IS NOT OLD.kind
    OR NEW.firm_id IS NOT OLD.firm_id
    OR NEW.company_id IS NOT OLD.company_id
    OR NEW.book_id IS NOT OLD.book_id
    OR NEW.period IS NOT OLD.period
    OR NEW.created_by IS NOT OLD.created_by
    OR NEW.created_at IS NOT OLD.created_at
  BEGIN
    SELECT RAISE(ABORT,'assistant answer snapshots are immutable');
  END;

CREATE TRIGGER IF NOT EXISTS assistant_rule_proposals_no_delete
  BEFORE DELETE ON assistant_rule_proposals
  BEGIN
    SELECT RAISE(ABORT,'assistant rule proposals are retained for audit');
  END;
CREATE TRIGGER IF NOT EXISTS assistant_rule_proposals_frozen
  BEFORE UPDATE ON assistant_rule_proposals
  WHEN NEW.payload_json IS NOT OLD.payload_json
    OR NEW.rule_key IS NOT OLD.rule_key
    OR NEW.version IS NOT OLD.version
    OR NEW.proposed_by IS NOT OLD.proposed_by
    OR NEW.firm_id IS NOT OLD.firm_id
    OR NEW.company_id IS NOT OLD.company_id
    OR NEW.created_at IS NOT OLD.created_at
  BEGIN
    SELECT RAISE(ABORT,'assistant rule content is frozen per version');
  END;
