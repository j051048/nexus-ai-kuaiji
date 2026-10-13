-- SQLite is the local demonstration store; production additionally enforces
-- actor-bound, same-transaction native confirmation in PostgreSQL.
CREATE TABLE assistant_action_plans(
 id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('ocr_batch','proposal_batch','bank_drafts')),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json) AND json_type(snapshot_json)='object'),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 0 AND 100),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 request_key TEXT NOT NULL CHECK(length(trim(request_key)) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 UNIQUE(conversation_id,created_by,request_key),UNIQUE(id,conversation_id,firm_id,company_id,created_by),
 FOREIGN KEY(conversation_id,firm_id,company_id,created_by) REFERENCES assistant_conversations(id,firm_id,company_id,created_by)
);
CREATE TABLE assistant_action_receipts(
 id TEXT PRIMARY KEY,plan_id TEXT NOT NULL UNIQUE,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 request_key TEXT NOT NULL CHECK(length(trim(request_key)) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object'),created_at TEXT NOT NULL,
 FOREIGN KEY(plan_id,conversation_id,firm_id,company_id,created_by) REFERENCES assistant_action_plans(id,conversation_id,firm_id,company_id,created_by)
);
CREATE INDEX assistant_action_plans_scope ON assistant_action_plans(conversation_id,created_by,created_at,id);
CREATE TRIGGER assistant_action_plans_create BEFORE INSERT ON assistant_action_plans BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assistant_conversations c JOIN users u ON u.id=c.created_by AND u.active=1
  WHERE c.id=NEW.conversation_id AND c.firm_id=NEW.firm_id AND c.company_id=NEW.company_id AND c.created_by=NEW.created_by
  AND json_extract(NEW.snapshot_json,'$.scope.company_id')=c.company_id
  AND json_extract(NEW.snapshot_json,'$.scope.book_ids')=json(c.book_ids_json)
  AND json_extract(NEW.snapshot_json,'$.scope.event_ids')=json(c.event_ids_json)
  AND json_extract(NEW.snapshot_json,'$.scope.draft_id') IS c.draft_id
  AND json_extract(NEW.snapshot_json,'$.scope_version')=c.scope_version)
  OR json_extract(NEW.snapshot_json,'$.kind') IS NOT NEW.kind OR json_type(NEW.snapshot_json,'$.items') IS NOT 'array'
  OR json_array_length(NEW.snapshot_json,'$.items')<>NEW.item_count OR julianday(NEW.expires_at)-julianday(NEW.created_at) NOT BETWEEN (30.0/1440-0.000001) AND (30.0/1440+0.000001)
 THEN RAISE(ABORT,'Assistant preview scope or input count is invalid') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.snapshot_json,'$.items') x
  WHERE (NEW.kind='bank_drafts' AND NOT EXISTS(SELECT 1 FROM sources s JOIN books b ON b.id=s.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period
   WHERE s.id=json_extract(x.value,'$.source_id') AND s.book_id=json_extract(x.value,'$.book_id') AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND s.source_kind='bank' AND p.status='open' AND NOT EXISTS(SELECT 1 FROM drafts d WHERE d.source_id=s.id)
   AND EXISTS(SELECT 1 FROM json_each(NEW.snapshot_json,'$.scope.book_ids') bs WHERE bs.value=b.id)))
  OR (NEW.kind IN('ocr_batch','proposal_batch') AND NOT EXISTS(SELECT 1 FROM business_events e WHERE e.id=json_extract(x.value,'$.event_id') AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.version=json_extract(x.value,'$.expected_version') AND e.fact_version=json_extract(x.value,'$.expected_fact_version') AND e.payload=json_extract(x.value,'$.event_payload'))))
 THEN RAISE(ABORT,'Assistant preview contains mismatched input facts') END;
END;
CREATE TRIGGER assistant_action_plans_immutable_update BEFORE UPDATE ON assistant_action_plans BEGIN SELECT RAISE(ABORT,'Assistant previews are immutable');END;
CREATE TRIGGER assistant_action_plans_immutable_delete BEFORE DELETE ON assistant_action_plans BEGIN SELECT RAISE(ABORT,'Assistant previews are immutable');END;
CREATE TRIGGER assistant_action_receipts_create BEFORE INSERT ON assistant_action_receipts BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assistant_action_plans p JOIN users u ON u.id=p.created_by AND u.active=1
  WHERE p.id=NEW.plan_id AND p.conversation_id=NEW.conversation_id AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.created_by=NEW.created_by
  AND p.item_count>0 AND json_extract(NEW.result_json,'$.kind')=p.kind AND json_extract(NEW.result_json,'$.count')=p.item_count
  AND julianday(NEW.created_at)>=julianday(p.created_at) AND julianday(NEW.created_at)<=julianday(p.expires_at))
 THEN RAISE(ABORT,'Assistant receipt requires a matching live preview') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM assistant_action_plans p WHERE p.id=NEW.plan_id AND
  ((p.kind='ocr_batch' AND (json_array_length(NEW.result_json,'$.jobs') IS NOT p.item_count
   OR NOT EXISTS(SELECT 1 FROM ocr_batches b WHERE b.id=json_extract(NEW.result_json,'$.batch_id') AND b.firm_id=p.firm_id AND b.company_id=p.company_id AND b.created_by=p.created_by AND b.idempotency_key='assistant:'||p.id)
   OR (SELECT COUNT(*) FROM ocr_jobs j WHERE j.batch_id=json_extract(NEW.result_json,'$.batch_id'))<>p.item_count
   OR EXISTS(SELECT 1 FROM json_each(p.snapshot_json,'$.items') x WHERE NOT EXISTS(SELECT 1 FROM ocr_jobs j WHERE j.batch_id=json_extract(NEW.result_json,'$.batch_id') AND j.event_id=json_extract(x.value,'$.event_id') AND j.attachment_id=json_extract(x.value,'$.attachment_id') AND j.expected_version=json_extract(x.value,'$.expected_version') AND j.expected_fact_version=json_extract(x.value,'$.expected_fact_version') AND j.attachment_sha256=json_extract(x.value,'$.attachment_sha256') AND j.created_by=p.created_by AND j.execution_user_id=p.created_by
    AND EXISTS(SELECT 1 FROM json_each(NEW.result_json,'$.jobs') r WHERE json_extract(r.value,'$.id')=j.id)))))
  OR (p.kind='proposal_batch' AND (json_array_length(NEW.result_json,'$.jobs') IS NOT p.item_count
   OR EXISTS(SELECT 1 FROM json_each(p.snapshot_json,'$.items') x WHERE NOT EXISTS(SELECT 1 FROM proposal_jobs j WHERE j.event_id=json_extract(x.value,'$.event_id') AND j.book_id=json_extract(x.value,'$.book_id') AND j.draft_id=json_extract(x.value,'$.draft_id') AND j.expected_fact_version=json_extract(x.value,'$.expected_fact_version') AND j.expected_draft_version=json_extract(x.value,'$.expected_draft_version') AND j.created_by=p.created_by AND j.execution_user_id=p.created_by AND julianday(j.created_at)>=julianday(p.created_at)
    AND EXISTS(SELECT 1 FROM json_each(NEW.result_json,'$.jobs') r WHERE json_extract(r.value,'$.id')=j.id)))))
  OR (p.kind='bank_drafts' AND (json_array_length(NEW.result_json,'$.draft_ids') IS NOT p.item_count OR json_extract(NEW.result_json,'$.created') IS NOT p.item_count
   OR EXISTS(SELECT 1 FROM json_each(p.snapshot_json,'$.items') x WHERE NOT EXISTS(SELECT 1 FROM drafts d WHERE d.source_id=json_extract(x.value,'$.source_id') AND d.book_id=json_extract(x.value,'$.book_id') AND d.status='draft' AND d.version=1 AND d.last_manual_edit_version=0
    AND EXISTS(SELECT 1 FROM json_each(NEW.result_json,'$.draft_ids') r WHERE r.value=d.id)
    AND EXISTS(SELECT 1 FROM audit_events a WHERE a.object_id=d.id AND a.action='draft.suggest' AND a.user_id=p.created_by AND a.firm_id=p.firm_id AND julianday(a.created_at)>=julianday(p.created_at))))))))
 THEN RAISE(ABORT,'Assistant receipt does not prove the fixed selected results') END;
END;
CREATE TRIGGER assistant_action_receipts_immutable_update BEFORE UPDATE ON assistant_action_receipts BEGIN SELECT RAISE(ABORT,'Assistant receipts are immutable');END;
CREATE TRIGGER assistant_action_receipts_immutable_delete BEFORE DELETE ON assistant_action_receipts BEGIN SELECT RAISE(ABORT,'Assistant receipts are immutable');END;
