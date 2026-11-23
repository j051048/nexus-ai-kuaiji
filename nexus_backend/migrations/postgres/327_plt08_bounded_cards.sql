-- compatibility: incremental
-- PLT08 有界动作卡：高风险复核清单与缺件催办草稿。
-- 只保存固定范围的预览与回执，不改单据状态、不发送、不授予权限；
-- 沿用 052/067 的护栏：不可变预览、64 位指纹、同事务人工确认、请求键幂等、30 分钟过期。
-- 旧表 assistant_action_plans / assistant_action_receipts 的校验证明保持原样，不在此重写。
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.assistant_card_plans(
 id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('review_queue_pull','missing_evidence_draft')),
 snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='object'),fingerprint TEXT NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 0 AND 100),reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 request_key TEXT NOT NULL CHECK(length(btrim(request_key)) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 UNIQUE(conversation_id,created_by,request_key),UNIQUE(id,conversation_id,firm_id,company_id,created_by),
 FOREIGN KEY(conversation_id,firm_id,company_id,created_by) REFERENCES accounting.assistant_conversations(id,firm_id,company_id,created_by)
);
CREATE INDEX IF NOT EXISTS assistant_card_plans_scope ON accounting.assistant_card_plans(conversation_id,created_by,created_at,id);

CREATE TABLE IF NOT EXISTS accounting.assistant_card_receipts(
 id TEXT PRIMARY KEY,plan_id TEXT NOT NULL UNIQUE REFERENCES accounting.assistant_card_plans(id),conversation_id TEXT NOT NULL,
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,request_key TEXT NOT NULL CHECK(length(btrim(request_key)) BETWEEN 8 AND 120),
 request_fingerprint TEXT NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),result_json TEXT NOT NULL CHECK(jsonb_typeof(result_json::jsonb)='object'),created_at TEXT NOT NULL,
 FOREIGN KEY(plan_id,conversation_id,firm_id,company_id,created_by) REFERENCES accounting.assistant_card_plans(id,conversation_id,firm_id,company_id,created_by)
);
CREATE INDEX IF NOT EXISTS assistant_card_receipts_scope ON accounting.assistant_card_receipts(conversation_id,created_at,id);

CREATE TABLE IF NOT EXISTS accounting.assistant_card_execution_claims(
 plan_id TEXT PRIMARY KEY REFERENCES accounting.assistant_card_plans(id),transaction_id BIGINT NOT NULL,
 confirmed_by TEXT NOT NULL REFERENCES accounting.users(id),request_key TEXT NOT NULL,created_at TEXT NOT NULL
);

ALTER TABLE accounting.assistant_card_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_card_plans FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assistant_card_plan_read ON accounting.assistant_card_plans;
CREATE POLICY assistant_card_plan_read ON accounting.assistant_card_plans FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));
DROP POLICY IF EXISTS assistant_card_plan_write ON accounting.assistant_card_plans;
CREATE POLICY assistant_card_plan_write ON accounting.assistant_card_plans FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));

ALTER TABLE accounting.assistant_card_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_card_receipts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assistant_card_receipt_read ON accounting.assistant_card_receipts;
CREATE POLICY assistant_card_receipt_read ON accounting.assistant_card_receipts FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));
DROP POLICY IF EXISTS assistant_card_receipt_write ON accounting.assistant_card_receipts;
CREATE POLICY assistant_card_receipt_write ON accounting.assistant_card_receipts FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));

ALTER TABLE accounting.assistant_card_execution_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_card_execution_claims FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assistant_card_claim_read ON accounting.assistant_card_execution_claims;
DROP POLICY IF EXISTS assistant_card_claim_write ON accounting.assistant_card_execution_claims;

REVOKE ALL ON accounting.assistant_card_plans FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON accounting.assistant_card_receipts FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON accounting.assistant_card_execution_claims FROM PUBLIC, accounting_app, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT,INSERT ON accounting.assistant_card_plans TO accounting_app;
GRANT SELECT,INSERT ON accounting.assistant_card_receipts TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.assistant_card_plan_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=accounting,pg_catalog AS $$
DECLARE c accounting.assistant_conversations%ROWTYPE;input JSONB;
BEGIN
 SELECT * INTO c FROM accounting.assistant_conversations WHERE id=NEW.conversation_id;
 input:=NEW.snapshot_json::jsonb;
 IF c.id IS NULL OR NEW.created_by<>accounting.actor_user() OR NEW.firm_id<>accounting.actor_firm()
  OR (NEW.firm_id,NEW.company_id,NEW.created_by) IS DISTINCT FROM (c.firm_id,c.company_id,c.created_by)
  OR input->>'kind' IS DISTINCT FROM NEW.kind OR input->>'scope_version' IS DISTINCT FROM c.scope_version::TEXT
  OR jsonb_typeof(input->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(input->'items')<>NEW.item_count
  OR input->'scope' IS DISTINCT FROM jsonb_build_object('company_id',c.company_id,'book_ids',c.book_ids_json::jsonb,'event_ids',c.event_ids_json::jsonb,'draft_id',c.draft_id)
  OR NEW.fingerprint<>encode(sha256(convert_to(NEW.snapshot_json,'UTF8')),'hex')
  OR NEW.expires_at::timestamptz IS DISTINCT FROM NEW.created_at::timestamptz+interval '30 minutes'
  THEN RAISE EXCEPTION 'Assistant card preview must bind current owner and scope' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION accounting.assistant_card_begin(target_plan TEXT,expected_fingerprint TEXT,confirmation_key TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=accounting,pg_catalog AS $$
DECLARE p accounting.assistant_card_plans%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903102);
 SELECT * INTO p FROM accounting.assistant_card_plans WHERE id=target_plan FOR UPDATE;
 IF p.id IS NULL OR p.created_by<>accounting.actor_user() OR p.firm_id<>accounting.actor_firm()
  OR p.fingerprint IS DISTINCT FROM expected_fingerprint OR p.item_count=0
  OR p.expires_at::timestamptz<=clock_timestamp() OR length(btrim(confirmation_key)) NOT BETWEEN 8 AND 120
  OR EXISTS(SELECT 1 FROM accounting.assistant_card_receipts r WHERE r.plan_id=p.id)
  THEN RAISE EXCEPTION 'Assistant card confirmation is expired, stale or already consumed' USING ERRCODE='23514';END IF;
 INSERT INTO accounting.assistant_card_execution_claims(plan_id,transaction_id,confirmed_by,request_key,created_at)
 VALUES(p.id,txid_current(),accounting.actor_user(),confirmation_key,to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'));
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION accounting.assistant_card_receipt_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=accounting,pg_catalog AS $$
DECLARE p accounting.assistant_card_plans%ROWTYPE;claim accounting.assistant_card_execution_claims%ROWTYPE;result JSONB;
BEGIN
 SELECT * INTO p FROM accounting.assistant_card_plans WHERE id=NEW.plan_id;
 SELECT * INTO claim FROM accounting.assistant_card_execution_claims WHERE plan_id=NEW.plan_id;
 result:=NEW.result_json::jsonb;
 IF p.id IS NULL OR claim.plan_id IS NULL OR claim.transaction_id<>txid_current()
  OR NEW.created_by<>accounting.actor_user() OR claim.confirmed_by<>accounting.actor_user() OR NEW.request_key IS DISTINCT FROM claim.request_key
  OR (NEW.conversation_id,NEW.firm_id,NEW.company_id,NEW.created_by) IS DISTINCT FROM (p.conversation_id,p.firm_id,p.company_id,p.created_by)
  OR result->>'kind' IS DISTINCT FROM p.kind OR result->>'count' IS DISTINCT FROM p.item_count::TEXT
  OR jsonb_typeof(result->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(result->'items')<>p.item_count
  OR result->>'created' IS DISTINCT FROM '0' OR jsonb_array_length(result->'jobs')<>0 OR jsonb_array_length(result->'draft_ids')<>0
  THEN RAISE EXCEPTION 'Assistant card receipt requires a same-transaction live confirmation' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION accounting.assistant_card_immutable_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=accounting,pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Assistant card previews and receipts are retained' USING ERRCODE='55000';END IF;
 RAISE EXCEPTION 'Assistant card previews and receipts are immutable' USING ERRCODE='55000';
END $$;

DROP TRIGGER IF EXISTS assistant_card_plans_create ON accounting.assistant_card_plans;
CREATE TRIGGER assistant_card_plans_create BEFORE INSERT ON accounting.assistant_card_plans FOR EACH ROW EXECUTE FUNCTION accounting.assistant_card_plan_guard();
DROP TRIGGER IF EXISTS assistant_card_plans_immutable ON accounting.assistant_card_plans;
CREATE TRIGGER assistant_card_plans_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_card_plans FOR EACH ROW EXECUTE FUNCTION accounting.assistant_card_immutable_guard();
DROP TRIGGER IF EXISTS assistant_card_receipts_create ON accounting.assistant_card_receipts;
CREATE TRIGGER assistant_card_receipts_create BEFORE INSERT ON accounting.assistant_card_receipts FOR EACH ROW EXECUTE FUNCTION accounting.assistant_card_receipt_guard();
DROP TRIGGER IF EXISTS assistant_card_receipts_immutable ON accounting.assistant_card_receipts;
CREATE TRIGGER assistant_card_receipts_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_card_receipts FOR EACH ROW EXECUTE FUNCTION accounting.assistant_card_immutable_guard();
DROP TRIGGER IF EXISTS assistant_card_claims_immutable ON accounting.assistant_card_execution_claims;
CREATE TRIGGER assistant_card_claims_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_card_execution_claims FOR EACH ROW EXECUTE FUNCTION accounting.assistant_card_immutable_guard();

REVOKE ALL ON FUNCTION accounting.assistant_card_plan_guard() FROM PUBLIC, accounting_app, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON FUNCTION accounting.assistant_card_begin(TEXT,TEXT,TEXT) FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT EXECUTE ON FUNCTION accounting.assistant_card_begin(TEXT,TEXT,TEXT) TO accounting_app;
REVOKE ALL ON FUNCTION accounting.assistant_card_receipt_guard() FROM PUBLIC, accounting_app, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON FUNCTION accounting.assistant_card_immutable_guard() FROM PUBLIC, accounting_app, accounting_worker, accounting_system, accounting_projection;

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.assistant_card_plans;
CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.assistant_card_plans FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_statement_write_gate ON accounting.assistant_card_plans;
CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT ON accounting.assistant_card_plans FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.assistant_card_receipts;
CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.assistant_card_receipts FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_statement_write_gate ON accounting.assistant_card_receipts;
CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT ON accounting.assistant_card_receipts FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
