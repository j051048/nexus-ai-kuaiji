-- compatibility: incremental
-- PLT08 有界动作卡：高风险复核清单与缺件催办草稿。
-- 仅保存固定范围预览与回执：不改单据状态、不发送、不授予权限。
-- 沿用 052 的护栏：不可变预览、64 位指纹、人工确认、请求键幂等、30 分钟过期。
CREATE TABLE assistant_card_plans(
 id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('review_queue_pull','missing_evidence_draft')),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json) AND json_type(snapshot_json)='object'),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 0 AND 100),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 request_key TEXT NOT NULL CHECK(length(trim(request_key)) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 UNIQUE(conversation_id,created_by,request_key),UNIQUE(id,conversation_id,firm_id,company_id,created_by),
 FOREIGN KEY(conversation_id,firm_id,company_id,created_by) REFERENCES assistant_conversations(id,firm_id,company_id,created_by)
);
CREATE TABLE assistant_card_receipts(
 id TEXT PRIMARY KEY,plan_id TEXT NOT NULL UNIQUE,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 request_key TEXT NOT NULL CHECK(length(trim(request_key)) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object'),created_at TEXT NOT NULL,
 FOREIGN KEY(plan_id,conversation_id,firm_id,company_id,created_by) REFERENCES assistant_card_plans(id,conversation_id,firm_id,company_id,created_by)
);
CREATE INDEX assistant_card_plans_scope ON assistant_card_plans(conversation_id,created_by,created_at,id);
CREATE INDEX assistant_card_receipts_scope ON assistant_card_receipts(conversation_id,created_at,id);
CREATE TRIGGER assistant_card_plans_create BEFORE INSERT ON assistant_card_plans BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assistant_conversations c JOIN users u ON u.id=c.created_by AND u.active=1
  WHERE c.id=NEW.conversation_id AND c.firm_id=NEW.firm_id AND c.company_id=NEW.company_id AND c.created_by=NEW.created_by
  AND json_extract(NEW.snapshot_json,'$.scope.company_id')=c.company_id
  AND json_extract(NEW.snapshot_json,'$.scope.book_ids')=json(c.book_ids_json)
  AND json_extract(NEW.snapshot_json,'$.scope.event_ids')=json(c.event_ids_json)
  AND json_extract(NEW.snapshot_json,'$.scope.draft_id') IS c.draft_id
  AND json_extract(NEW.snapshot_json,'$.scope_version')=c.scope_version)
  OR json_extract(NEW.snapshot_json,'$.kind') IS NOT NEW.kind OR json_type(NEW.snapshot_json,'$.items') IS NOT 'array'
  OR json_array_length(NEW.snapshot_json,'$.items')<>NEW.item_count
  OR julianday(NEW.expires_at)-julianday(NEW.created_at) NOT BETWEEN (30.0/1440-0.000001) AND (30.0/1440+0.000001)
  THEN RAISE(ABORT,'Assistant card preview scope or input count is invalid') END;
END;
CREATE TRIGGER assistant_card_plans_immutable_update BEFORE UPDATE ON assistant_card_plans BEGIN SELECT RAISE(ABORT,'Assistant card previews are immutable');END;
CREATE TRIGGER assistant_card_plans_immutable_delete BEFORE DELETE ON assistant_card_plans BEGIN SELECT RAISE(ABORT,'Assistant card previews are immutable');END;
CREATE TRIGGER assistant_card_receipts_create BEFORE INSERT ON assistant_card_receipts BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assistant_card_plans p JOIN users u ON u.id=p.created_by AND u.active=1
  WHERE p.id=NEW.plan_id AND p.conversation_id=NEW.conversation_id AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.created_by=NEW.created_by
  AND p.item_count>0 AND json_extract(NEW.result_json,'$.kind')=p.kind AND json_extract(NEW.result_json,'$.count')=p.item_count
  AND json_array_length(NEW.result_json,'$.items')=p.item_count
  AND json_extract(NEW.result_json,'$.created')=0
  AND json_array_length(NEW.result_json,'$.jobs')=0 AND json_array_length(NEW.result_json,'$.draft_ids')=0
  AND julianday(NEW.created_at)>=julianday(p.created_at) AND julianday(NEW.created_at)<=julianday(p.expires_at))
  THEN RAISE(ABORT,'Assistant card receipt requires a matching live preview') END;
END;
CREATE TRIGGER assistant_card_receipts_immutable_update BEFORE UPDATE ON assistant_card_receipts BEGIN SELECT RAISE(ABORT,'Assistant card receipts are immutable');END;
CREATE TRIGGER assistant_card_receipts_immutable_delete BEFORE DELETE ON assistant_card_receipts BEGIN SELECT RAISE(ABORT,'Assistant card receipts are immutable');END;
