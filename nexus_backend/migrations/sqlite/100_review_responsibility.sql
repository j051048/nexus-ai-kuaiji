-- compatibility: breaking
-- Versioned review responsibility. Old facts remain explicitly unverified.
CREATE TABLE ledger_review_policies(ledger_id TEXT NOT NULL REFERENCES ledgers(id),version INTEGER NOT NULL CHECK(version>0),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN('independent','single_operator')),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),confirmed_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(ledger_id,version));
CREATE TABLE draft_preparations(draft_id TEXT PRIMARY KEY REFERENCES drafts(id),book_id TEXT NOT NULL REFERENCES books(id),version INTEGER NOT NULL,maker_user_id TEXT REFERENCES users(id),content_revision INTEGER NOT NULL CHECK(content_revision>0),updated_at TEXT NOT NULL,version_transaction TEXT NOT NULL DEFAULT '');
CREATE TABLE draft_preparation_history(draft_id TEXT NOT NULL REFERENCES drafts(id),content_revision INTEGER NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),version INTEGER NOT NULL,maker_user_id TEXT REFERENCES users(id),changed_at TEXT NOT NULL,transaction_id TEXT NOT NULL DEFAULT '',PRIMARY KEY(draft_id,content_revision));
CREATE TABLE draft_review_receipts(draft_id TEXT NOT NULL REFERENCES drafts(id),reviewed_version INTEGER NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),maker_user_id TEXT NOT NULL REFERENCES users(id),reviewer_user_id TEXT NOT NULL REFERENCES users(id),policy_version INTEGER NOT NULL,mode TEXT NOT NULL CHECK(mode IN('independent','single_operator')),independent INTEGER NOT NULL CHECK(independent IN(0,1)),reason TEXT NOT NULL,reviewed_at TEXT NOT NULL,content_revision INTEGER NOT NULL,content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),PRIMARY KEY(draft_id,reviewed_version),FOREIGN KEY(draft_id,content_revision) REFERENCES draft_preparation_history(draft_id,content_revision),CHECK((mode='independent' AND independent=1 AND maker_user_id<>reviewer_user_id) OR (mode='single_operator' AND independent=0)));
CREATE TABLE manual_review_requests(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),version INTEGER NOT NULL CHECK(version>0),prepared_by TEXT NOT NULL REFERENCES users(id),status TEXT NOT NULL CHECK(status IN('pending','approved')),policy_version INTEGER NOT NULL CHECK(policy_version>=0),content_json TEXT NOT NULL CHECK(json_valid(content_json)),content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),command_key TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(trim(reason))>=3),reviewed_by TEXT REFERENCES users(id),reviewed_at TEXT,independent INTEGER NOT NULL DEFAULT 0 CHECK(independent IN(0,1)),review_reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(book_id,command_key));
CREATE TABLE voucher_review_completions(id TEXT PRIMARY KEY REFERENCES vouchers(id),book_id TEXT NOT NULL REFERENCES books(id),command_id TEXT NOT NULL UNIQUE REFERENCES posting_commands(id),content_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL);
ALTER TABLE drafts ADD COLUMN preparation_claim_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE drafts ADD COLUMN review_policy_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE drafts ADD COLUMN review_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE vouchers ADD COLUMN review_request_id TEXT REFERENCES manual_review_requests(id);
ALTER TABLE vouchers ADD COLUMN review_mode TEXT NOT NULL DEFAULT 'legacy_unverified';
ALTER TABLE vouchers ADD COLUMN review_maker_id TEXT REFERENCES users(id);
ALTER TABLE vouchers ADD COLUMN review_reviewer_id TEXT REFERENCES users(id);
ALTER TABLE vouchers ADD COLUMN voucher_review_policy_version INTEGER;
ALTER TABLE vouchers ADD COLUMN periodic_review_id TEXT REFERENCES periodic_drafts(id);
ALTER TABLE vouchers ADD COLUMN review_completion_id TEXT REFERENCES voucher_review_completions(id) DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX rr_manual_once ON vouchers(review_request_id) WHERE review_request_id IS NOT NULL;
CREATE UNIQUE INDEX rr_periodic_once ON vouchers(periodic_review_id) WHERE periodic_review_id IS NOT NULL;
-- Never infer historical human authorship from approval, import owner, or job runner.
INSERT INTO draft_preparations SELECT id,book_id,version,NULL,1,strftime('%Y-%m-%dT%H:%M:%fZ','now'),'' FROM drafts;
INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,'' FROM draft_preparations;
CREATE VIEW rr_book_policies AS SELECT lp.book_id,lp.ledger_id,COALESCE(p.version,0) AS version,COALESCE(p.mode,'independent') AS mode FROM ledger_periods lp LEFT JOIN ledger_review_policies p ON p.ledger_id=lp.ledger_id AND p.version=(SELECT MAX(x.version) FROM ledger_review_policies x WHERE x.ledger_id=lp.ledger_id);
CREATE VIEW rr_draft_contents AS SELECT d.id AS draft_id,accounting_sha256(json_array(d.book_id,d.source_id,d.description,d.explanation,(SELECT json_group_array(json(line)) FROM (SELECT json_array(l.ordinal,l.account_code,l.debit_cents,l.credit_cents,json(COALESCE(a.values_json,'{}'))) AS line FROM draft_lines l LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal WHERE l.draft_id=d.id ORDER BY l.ordinal)))) AS fingerprint FROM drafts d;
CREATE TRIGGER rr_policy_insert BEFORE INSERT ON ledger_review_policies BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ledgers l JOIN memberships m ON m.firm_id=l.firm_id AND m.user_id=accounting_actor_user() JOIN users u ON u.id=m.user_id AND u.active=1 JOIN firm_clients fc ON fc.firm_id=l.firm_id AND fc.company_id=l.company_id AND fc.status='active' JOIN companies c ON c.id=l.company_id AND c.status='active' WHERE l.id=NEW.ledger_id AND l.firm_id=NEW.firm_id AND l.company_id=NEW.company_id AND l.firm_id=accounting_actor_firm() AND m.role IN('owner','admin') AND NEW.confirmed_by=accounting_actor_user()) OR NEW.version<>COALESCE((SELECT MAX(version)+1 FROM ledger_review_policies WHERE ledger_id=NEW.ledger_id),1) THEN RAISE(ABORT,'Current ledger manager and next policy version required') END;
END;
CREATE TRIGGER rr_policy_revision AFTER INSERT ON ledger_review_policies BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER rr_policy_update BEFORE UPDATE ON ledger_review_policies BEGIN SELECT RAISE(ABORT,'Review policy immutable'); END;
CREATE TRIGGER rr_policy_delete BEFORE DELETE ON ledger_review_policies BEGIN SELECT RAISE(ABORT,'Review policy immutable'); END;
CREATE TRIGGER rr_draft_insert_guard BEFORE INSERT ON drafts BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NEW.status<>'draft' OR NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL OR NEW.exported_at IS NOT NULL THEN RAISE(ABORT,'New draft cannot carry approval') END;
END;
CREATE TRIGGER rr_draft_insert AFTER INSERT ON drafts BEGIN
 INSERT INTO draft_preparations VALUES(NEW.id,NEW.book_id,NEW.version,NULLIF(accounting_actor_user(),''),1,strftime('%Y-%m-%dT%H:%M:%fZ','now'),accounting_transaction_id());
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=NEW.id;
END;
CREATE TRIGGER rr_draft_update_guard BEFORE UPDATE ON drafts BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NEW.id<>OLD.id OR NEW.book_id<>OLD.book_id OR NEW.source_id<>OLD.source_id OR NEW.version<OLD.version THEN RAISE(ABORT,'Draft identity and monotonic version required') END;
 SELECT CASE WHEN OLD.status<>'draft' AND NOT COALESCE((OLD.status='approved' AND NEW.status='exported' AND NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.explanation=OLD.explanation AND NEW.approved_by IS OLD.approved_by AND NEW.approved_at IS OLD.approved_at AND NEW.review_policy_version=OLD.review_policy_version AND NEW.review_reason=OLD.review_reason AND NEW.preparation_claim_reason=OLD.preparation_claim_reason AND NEW.last_manual_edit_version=OLD.last_manual_edit_version AND EXISTS(SELECT 1 FROM draft_review_receipts r JOIN rr_draft_contents c ON c.draft_id=r.draft_id AND c.fingerprint=r.content_fingerprint WHERE r.draft_id=OLD.id AND r.reviewed_version=OLD.version-1)),0) THEN RAISE(ABORT,'Reviewed draft content immutable or legacy receipt missing') END;
 SELECT CASE WHEN NEW.preparation_claim_reason<>OLD.preparation_claim_reason AND (OLD.status<>'draft' OR length(trim(NEW.preparation_claim_reason))<3 OR NEW.version<>OLD.version+1 OR NOT EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=OLD.id AND p.maker_user_id IS NULL) OR NOT EXISTS(SELECT 1 FROM service_book_user_permissions p WHERE p.book_id=OLD.book_id AND p.user_id=accounting_actor_user() AND p.can_edit)) THEN RAISE(ABORT,'Only unattributed pending preparation can be claimed') END;
 SELECT CASE WHEN OLD.status='draft' AND NEW.status='approved' AND NOT COALESCE((
  NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.explanation=OLD.explanation AND NEW.approved_by=accounting_actor_user() AND NEW.approved_at IS NOT NULL
  AND EXISTS(SELECT 1 FROM service_book_user_permissions p JOIN books b ON b.id=p.book_id WHERE p.book_id=OLD.book_id AND b.firm_id=accounting_actor_firm() AND p.user_id=accounting_actor_user() AND p.can_review)
  AND EXISTS(SELECT 1 FROM draft_preparations p JOIN rr_book_policies pol ON pol.book_id=p.book_id WHERE p.draft_id=OLD.id AND p.version=OLD.version AND p.maker_user_id IS NOT NULL AND pol.version=NEW.review_policy_version AND ((pol.mode='independent' AND p.maker_user_id<>accounting_actor_user()) OR (pol.mode='single_operator' AND length(trim(NEW.review_reason))>=3)))
  AND NOT EXISTS(SELECT 1 FROM business_mappings bm JOIN business_events e ON e.id=bm.event_id WHERE bm.draft_id=OLD.id AND e.applicant_id=accounting_actor_user())
 ),0) THEN RAISE(ABORT,'Current independent reviewer and policy required') END;
 SELECT CASE WHEN OLD.status='draft' AND NEW.status NOT IN('draft','approved') THEN RAISE(ABORT,'Approval receipt required') END;
END;
CREATE TRIGGER rr_draft_update AFTER UPDATE ON drafts WHEN OLD.status='draft' AND NEW.status='draft' AND (NEW.version<>OLD.version OR NEW.description<>OLD.description OR NEW.explanation<>OLD.explanation OR NEW.preparation_claim_reason<>OLD.preparation_claim_reason) BEGIN
 UPDATE draft_preparations SET version=NEW.version,version_transaction=CASE WHEN NEW.version<>OLD.version THEN accounting_transaction_id() ELSE version_transaction END,maker_user_id=CASE WHEN NEW.description<>OLD.description OR NEW.explanation<>OLD.explanation OR NEW.preparation_claim_reason<>OLD.preparation_claim_reason THEN NULLIF(accounting_actor_user(),'') ELSE maker_user_id END,content_revision=content_revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE draft_id=NEW.id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=NEW.id;
 UPDATE drafts SET version=version+1 WHERE id=NEW.id AND NEW.version=OLD.version AND (NEW.description<>OLD.description OR NEW.explanation<>OLD.explanation) AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.id AND p.version_transaction<>accounting_transaction_id());
END;
CREATE TRIGGER rr_draft_approve AFTER UPDATE ON drafts WHEN OLD.status='draft' AND NEW.status='approved' BEGIN
 INSERT INTO draft_review_receipts SELECT NEW.id,OLD.version,NEW.book_id,p.maker_user_id,accounting_actor_user(),pol.version,pol.mode,CASE WHEN pol.mode='independent' THEN 1 ELSE 0 END,NEW.review_reason,NEW.approved_at,p.content_revision,c.fingerprint FROM draft_preparations p JOIN rr_book_policies pol ON pol.book_id=p.book_id JOIN rr_draft_contents c ON c.draft_id=p.draft_id WHERE p.draft_id=NEW.id;
END;
CREATE TRIGGER rr_line_insert_guard BEFORE INSERT ON draft_lines BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE(ABORT,'Only pending draft content can change') END;
END;

CREATE TRIGGER rr_line_insert AFTER INSERT ON draft_lines BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting_actor_user(),''),content_revision=content_revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
END;

CREATE TRIGGER rr_line_update_guard BEFORE UPDATE ON draft_lines WHEN NEW.draft_id IS NOT OLD.draft_id OR NEW.book_id IS NOT OLD.book_id OR NEW.ordinal IS NOT OLD.ordinal OR NEW.account_code IS NOT OLD.account_code OR NEW.debit_cents IS NOT OLD.debit_cents OR NEW.credit_cents IS NOT OLD.credit_cents BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE(ABORT,'Only pending draft content can change') END;
 SELECT CASE WHEN NEW.draft_id<>OLD.draft_id OR NEW.book_id<>OLD.book_id OR NEW.ordinal<>OLD.ordinal THEN RAISE(ABORT,'Draft line identity immutable') END;
END;

CREATE TRIGGER rr_line_update AFTER UPDATE ON draft_lines WHEN NEW.draft_id IS NOT OLD.draft_id OR NEW.book_id IS NOT OLD.book_id OR NEW.ordinal IS NOT OLD.ordinal OR NEW.account_code IS NOT OLD.account_code OR NEW.debit_cents IS NOT OLD.debit_cents OR NEW.credit_cents IS NOT OLD.credit_cents BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting_actor_user(),''),content_revision=content_revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
END;

CREATE TRIGGER rr_line_delete_guard BEFORE DELETE ON draft_lines BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id AND status='draft') THEN RAISE(ABORT,'Only pending draft content can change') END;
END;

CREATE TRIGGER rr_line_delete AFTER DELETE ON draft_lines BEGIN
 UPDATE drafts SET version=version+1 WHERE id=OLD.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=OLD.draft_id AND p.version_transaction<>accounting_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting_actor_user(),''),content_revision=content_revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE draft_id=OLD.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=OLD.draft_id;
END;

CREATE TRIGGER rr_aux_insert_guard BEFORE INSERT ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE(ABORT,'Only pending draft content can change') END;
END;

CREATE TRIGGER rr_aux_insert AFTER INSERT ON draft_line_auxiliaries BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting_actor_user(),''),content_revision=content_revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
END;

CREATE TRIGGER rr_aux_update_guard BEFORE UPDATE ON draft_line_auxiliaries WHEN NEW.draft_id IS NOT OLD.draft_id OR NEW.book_id IS NOT OLD.book_id OR NEW.ordinal IS NOT OLD.ordinal OR NEW.values_json IS NOT OLD.values_json BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE(ABORT,'Only pending draft content can change') END;
 SELECT CASE WHEN NEW.draft_id<>OLD.draft_id OR NEW.book_id<>OLD.book_id OR NEW.ordinal<>OLD.ordinal THEN RAISE(ABORT,'Draft line identity immutable') END;
END;

CREATE TRIGGER rr_aux_update AFTER UPDATE ON draft_line_auxiliaries WHEN NEW.draft_id IS NOT OLD.draft_id OR NEW.book_id IS NOT OLD.book_id OR NEW.ordinal IS NOT OLD.ordinal OR NEW.values_json IS NOT OLD.values_json BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting_actor_user(),''),content_revision=content_revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
END;

CREATE TRIGGER rr_aux_delete_guard BEFORE DELETE ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id AND status='draft') THEN RAISE(ABORT,'Only pending draft content can change') END;
END;

CREATE TRIGGER rr_aux_delete AFTER DELETE ON draft_line_auxiliaries BEGIN
 UPDATE drafts SET version=version+1 WHERE id=OLD.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=OLD.draft_id AND p.version_transaction<>accounting_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting_actor_user(),''),content_revision=content_revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE draft_id=OLD.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting_transaction_id() FROM draft_preparations WHERE draft_id=OLD.draft_id;
END;

CREATE TRIGGER rr_draft_preparation_history_update BEFORE UPDATE ON draft_preparation_history BEGIN SELECT RAISE(ABORT,'Review evidence immutable'); END;

CREATE TRIGGER rr_draft_preparation_history_delete BEFORE DELETE ON draft_preparation_history BEGIN SELECT RAISE(ABORT,'Review evidence immutable'); END;

CREATE TRIGGER rr_draft_review_receipts_update BEFORE UPDATE ON draft_review_receipts BEGIN SELECT RAISE(ABORT,'Review evidence immutable'); END;

CREATE TRIGGER rr_draft_review_receipts_delete BEFORE DELETE ON draft_review_receipts BEGIN SELECT RAISE(ABORT,'Review evidence immutable'); END;

CREATE TRIGGER rr_voucher_review_completions_update BEFORE UPDATE ON voucher_review_completions BEGIN SELECT RAISE(ABORT,'Review evidence immutable'); END;

CREATE TRIGGER rr_voucher_review_completions_delete BEFORE DELETE ON voucher_review_completions BEGIN SELECT RAISE(ABORT,'Review evidence immutable'); END;

CREATE TRIGGER rr_manual_insert BEFORE INSERT ON manual_review_requests BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;

 SELECT CASE WHEN NOT COALESCE((json_type(NEW.content_json,'$.voucher_date')='text' AND length(json_extract(NEW.content_json,'$.voucher_date'))=10 AND substr(json_extract(NEW.content_json,'$.voucher_date'),1,7)=(SELECT period FROM books WHERE id=NEW.book_id) AND json_extract(NEW.content_json,'$.voucher_type') IN('记','收','付','转') AND json_type(NEW.content_json,'$.description')='text' AND length(trim(json_extract(NEW.content_json,'$.description'))) BETWEEN 1 AND 500 AND json_extract(NEW.content_json,'$.reason')=NEW.reason AND json_type(NEW.content_json,'$.relation_kind') IN('null','text') AND json_type(NEW.content_json,'$.original_voucher_id') IN('null','text')
 AND ((json_extract(NEW.content_json,'$.relation_kind') IS NULL AND json_extract(NEW.content_json,'$.original_voucher_id') IS NULL) OR (json_extract(NEW.content_json,'$.relation_kind') IN('adjustment','reversal') AND EXISTS(SELECT 1 FROM vouchers WHERE id=json_extract(NEW.content_json,'$.original_voucher_id') AND book_id=NEW.book_id)))
 AND NOT EXISTS(SELECT 1 FROM json_each(NEW.content_json,'$.lines') j WHERE NOT COALESCE((json_type(j.value,'$.account_id')='text' AND json_type(j.value,'$.account_version')='integer' AND json_extract(j.value,'$.account_version')>0 AND json_type(j.value,'$.debit')='text' AND json_type(j.value,'$.credit')='text' AND json_type(j.value,'$.auxiliary_values')='object' AND json_type(j.value,'$.source_ref')='text' AND ((accounting_money_cents(json_extract(j.value,'$.debit'))>0 AND accounting_money_cents(json_extract(j.value,'$.credit'))=0) OR (accounting_money_cents(json_extract(j.value,'$.credit'))>0 AND accounting_money_cents(json_extract(j.value,'$.debit'))=0))),0))
 AND (SELECT SUM(accounting_money_cents(json_extract(j.value,'$.debit'))-accounting_money_cents(json_extract(j.value,'$.credit'))) FROM json_each(NEW.content_json,'$.lines') j)=0),0) THEN RAISE(ABORT,'Complete balanced manual payload required') END;
 SELECT CASE WHEN NOT COALESCE((NEW.status='pending' AND NEW.version=1 AND NEW.prepared_by=accounting_actor_user() AND NEW.reviewed_by IS NULL AND NEW.reviewed_at IS NULL AND NEW.independent=0
 AND EXISTS(SELECT 1 FROM service_book_user_permissions p JOIN books b ON b.id=p.book_id WHERE p.book_id=NEW.book_id AND b.firm_id=accounting_actor_firm() AND p.user_id=accounting_actor_user() AND p.can_edit AND EXISTS(SELECT 1 FROM periods pr WHERE pr.book_id=b.id AND pr.status='open') AND NOT EXISTS(SELECT 1 FROM internal_period_locks lk WHERE lk.book_id=b.id))
 AND EXISTS(SELECT 1 FROM rr_book_policies p WHERE p.book_id=NEW.book_id AND p.version=NEW.policy_version)
 AND NEW.content_fingerprint=accounting_sha256(NEW.content_json) AND json_type(NEW.content_json)='object' AND json_extract(NEW.content_json,'$.book_id')=NEW.book_id AND json_extract(NEW.content_json,'$.command_key')=NEW.command_key
 AND json_type(NEW.content_json,'$.lines')='array' AND json_array_length(NEW.content_json,'$.lines')>=2 AND json_type(NEW.content_json,'$.review_request_id') IS NULL
 ),0) THEN RAISE(ABORT,'Trusted preparer and complete current manual request required') END;
END;
CREATE TRIGGER rr_manual_update BEFORE UPDATE ON manual_review_requests BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT COALESCE((OLD.status='pending' AND NEW.status='approved' AND NEW.version=OLD.version+1 AND NEW.id=OLD.id AND NEW.book_id=OLD.book_id AND NEW.prepared_by=OLD.prepared_by AND NEW.policy_version=OLD.policy_version AND NEW.content_json=OLD.content_json AND NEW.content_fingerprint=OLD.content_fingerprint AND NEW.command_key=OLD.command_key AND NEW.reason=OLD.reason AND NEW.created_at=OLD.created_at AND NEW.reviewed_by=accounting_actor_user() AND NEW.reviewed_at IS NOT NULL
 AND EXISTS(SELECT 1 FROM service_book_user_permissions p JOIN books b ON b.id=p.book_id WHERE p.book_id=NEW.book_id AND b.firm_id=accounting_actor_firm() AND p.user_id=accounting_actor_user() AND p.can_review AND EXISTS(SELECT 1 FROM periods pr WHERE pr.book_id=b.id AND pr.status='open') AND NOT EXISTS(SELECT 1 FROM internal_period_locks lk WHERE lk.book_id=b.id))
 AND EXISTS(SELECT 1 FROM rr_book_policies p WHERE p.book_id=OLD.book_id AND p.version=OLD.policy_version AND ((p.mode='independent' AND NEW.independent=1 AND NEW.reviewed_by<>OLD.prepared_by) OR (p.mode='single_operator' AND NEW.independent=0 AND length(trim(NEW.review_reason))>=3)))
 ),0) THEN RAISE(ABORT,'Current independent manual review required; approved content immutable') END;
END;
CREATE TRIGGER rr_manual_delete BEFORE DELETE ON manual_review_requests BEGIN SELECT RAISE(ABORT,'Manual review evidence immutable'); END;

-- The expected line set is computed from immutable reviewed facts, never from a supplied hash.
CREATE VIEW rr_expected_voucher_lines AS
 SELECT v.id AS voucher_id,ROW_NUMBER() OVER(PARTITION BY v.id ORDER BY l.ordinal) AS line_no,m.account_id,m.account_version,l.debit_cents,l.credit_cents,COALESCE(a.values_json,'{}') AS aux_json,d.source_id AS source_ref
 FROM vouchers v JOIN drafts d ON d.id=v.source_id AND d.book_id=v.book_id JOIN draft_lines l ON l.draft_id=d.id
 JOIN ledger_account_mappings m ON m.book_id=d.book_id AND m.book_account_code=l.account_code
 LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal WHERE v.source_kind='draft' AND v.periodic_review_id IS NULL
 UNION ALL SELECT v.id,CAST(j.key AS INTEGER)+1,json_extract(j.value,'$.account_id'),json_extract(j.value,'$.account_version'),accounting_money_cents(json_extract(j.value,'$.debit')),accounting_money_cents(json_extract(j.value,'$.credit')),json_extract(j.value,'$.auxiliary_values'),json_extract(j.value,'$.source_ref')
 FROM vouchers v JOIN manual_review_requests r ON r.id=v.review_request_id,json_each(r.content_json,'$.lines') j WHERE v.periodic_review_id IS NULL AND v.source_kind IN('manual','adjustment','reversal')
 UNION ALL SELECT v.id,l.line_no,l.account_id,l.account_version,l.debit_cents,l.credit_cents,l.aux_json,l.source_ref FROM vouchers v JOIN periodic_draft_lines l ON l.draft_id=v.periodic_review_id WHERE v.periodic_review_id IS NOT NULL;
CREATE VIEW rr_verified_voucher_headers AS
 SELECT v.id FROM vouchers v JOIN drafts d ON d.id=v.source_id AND d.book_id=v.book_id JOIN draft_review_receipts r ON r.draft_id=d.id AND r.reviewed_version=CASE WHEN d.status='approved' THEN d.version-1 ELSE d.version-2 END JOIN rr_draft_contents dc ON dc.draft_id=d.id AND dc.fingerprint=r.content_fingerprint
 WHERE v.source_kind='draft' AND v.review_request_id IS NULL AND v.periodic_review_id IS NULL AND d.status IN('approved','exported') AND v.source_version=d.version AND v.relation_kind IS NULL AND v.reversal_of IS NULL AND v.description=d.description AND v.review_mode=r.mode AND v.review_maker_id=r.maker_user_id AND v.review_reviewer_id=r.reviewer_user_id AND v.voucher_review_policy_version=r.policy_version
 UNION ALL SELECT v.id FROM vouchers v JOIN manual_review_requests r ON r.id=v.review_request_id AND r.book_id=v.book_id JOIN ledger_periods lp ON lp.book_id=r.book_id LEFT JOIN ledger_review_policies p ON p.ledger_id=lp.ledger_id AND p.version=r.policy_version
 WHERE v.source_kind IN('manual','adjustment','reversal') AND v.periodic_review_id IS NULL AND v.source_version IS NULL AND v.source_id IS (CASE WHEN v.source_kind='reversal' THEN v.reversal_of ELSE NULL END) AND r.status='approved' AND v.review_mode=COALESCE(p.mode,'independent') AND (r.policy_version=0 OR p.version IS NOT NULL) AND v.review_maker_id=r.prepared_by AND v.review_reviewer_id=r.reviewed_by AND v.voucher_review_policy_version=r.policy_version
 AND v.voucher_date=json_extract(r.content_json,'$.voucher_date') AND v.voucher_type=json_extract(r.content_json,'$.voucher_type') AND v.description=json_extract(r.content_json,'$.description') AND v.relation_kind IS json_extract(r.content_json,'$.relation_kind') AND v.reversal_of IS json_extract(r.content_json,'$.original_voucher_id')
 AND v.source_kind=COALESCE(json_extract(r.content_json,'$.relation_kind'),'manual')
 UNION ALL SELECT v.id FROM vouchers v JOIN periodic_drafts d ON d.id=v.periodic_review_id AND d.book_id=v.book_id
 WHERE v.source_kind='manual' AND v.review_request_id IS NULL AND v.source_id IS NULL AND v.source_version IS NULL AND d.status='approved' AND d.created_by<>d.approved_by AND v.review_mode='independent' AND v.review_maker_id=d.created_by AND v.review_reviewer_id=d.approved_by AND v.voucher_review_policy_version=0 AND v.description=d.description AND v.voucher_type='转' AND v.relation_kind IS NULL AND v.reversal_of IS NULL;
CREATE TRIGGER rr_voucher_insert BEFORE INSERT ON vouchers BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NEW.review_completion_id IS NOT NEW.id OR NEW.review_mode NOT IN('independent','single_operator') OR NEW.posted_by<>accounting_actor_user() OR NOT EXISTS(SELECT 1 FROM service_book_user_permissions p JOIN books b ON b.id=p.book_id JOIN ledger_periods lp ON lp.book_id=b.id WHERE p.book_id=NEW.book_id AND p.user_id=accounting_actor_user() AND p.can_review AND EXISTS(SELECT 1 FROM periods pr WHERE pr.book_id=b.id AND pr.status='open') AND NOT EXISTS(SELECT 1 FROM internal_period_locks lk WHERE lk.book_id=b.id) AND b.firm_id=accounting_actor_firm() AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND b.period=NEW.period AND substr(NEW.voucher_date,1,7)=b.period AND lp.ledger_id=NEW.ledger_id AND EXISTS(SELECT 1 FROM opening_bindings ob WHERE ob.id=NEW.opening_binding_id AND ob.book_id=b.id AND ob.ledger_id=lp.ledger_id AND ob.firm_id=b.firm_id AND ob.company_id=b.company_id AND ob.period=b.period AND ob.status='active')) THEN RAISE(ABORT,'Current reviewer and deferred review completion required') END;
END;
CREATE TRIGGER rr_voucher_line_insert BEFORE INSERT ON voucher_lines BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM voucher_review_completions WHERE id=NEW.voucher_id) OR EXISTS(SELECT 1 FROM vouchers WHERE id=NEW.voucher_id AND review_completion_id IS NULL) THEN RAISE(ABORT,'Completed or legacy voucher lines immutable') END;
END;
CREATE TRIGGER rr_posting_complete AFTER INSERT ON posting_commands BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM vouchers v JOIN rr_verified_voucher_headers h ON h.id=v.id WHERE v.id=NEW.voucher_id AND v.command_id=NEW.id AND v.book_id=NEW.book_id AND v.firm_id=NEW.firm_id AND v.company_id=NEW.company_id AND v.posted_by=NEW.created_by AND NEW.created_by=accounting_actor_user() AND v.review_completion_id=v.id AND (v.review_request_id IS NULL OR EXISTS(SELECT 1 FROM manual_review_requests r WHERE r.id=v.review_request_id AND r.command_key=NEW.command_key))) THEN RAISE(ABORT,'Voucher header must match frozen review') END;
 SELECT CASE WHEN (SELECT COUNT(*) FROM rr_expected_voucher_lines WHERE voucher_id=NEW.voucher_id)<2 OR (SELECT COUNT(*) FROM voucher_lines WHERE voucher_id=NEW.voucher_id)<>(SELECT COUNT(*) FROM rr_expected_voucher_lines WHERE voucher_id=NEW.voucher_id) OR EXISTS(SELECT 1 FROM rr_expected_voucher_lines e LEFT JOIN voucher_lines l ON l.voucher_id=e.voucher_id AND l.line_no=e.line_no WHERE e.voucher_id=NEW.voucher_id AND (l.id IS NULL OR l.account_id IS NOT e.account_id OR l.account_version IS NOT e.account_version OR l.debit_cents IS NOT e.debit_cents OR l.credit_cents IS NOT e.credit_cents OR json(l.aux_json) IS NOT json(e.aux_json) OR l.source_ref IS NOT e.source_ref)) OR (SELECT SUM(debit_cents-credit_cents) FROM voucher_lines WHERE voucher_id=NEW.voucher_id)<>0 THEN RAISE(ABORT,'Voucher lines must exactly match frozen review') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM vouchers v WHERE v.id=NEW.voucher_id AND v.source_kind='reversal' AND ((SELECT COUNT(*) FROM voucher_lines WHERE voucher_id=v.id)<>(SELECT COUNT(*) FROM voucher_lines WHERE voucher_id=v.reversal_of) OR NOT EXISTS(SELECT 1 FROM vouchers o WHERE o.id=v.reversal_of AND o.book_id=v.book_id) OR EXISTS(SELECT 1 FROM voucher_lines l LEFT JOIN voucher_lines o ON o.voucher_id=v.reversal_of AND o.line_no=l.line_no WHERE l.voucher_id=v.id AND (o.id IS NULL OR l.account_id<>o.account_id OR l.account_version<>o.account_version OR l.debit_cents<>o.credit_cents OR l.credit_cents<>o.debit_cents OR json(l.aux_json)<>json(o.aux_json))))) THEN RAISE(ABORT,'Reversal must exactly reverse reviewed original') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id LEFT JOIN ledger_account_versions av ON av.account_id=l.account_id AND av.version=l.account_version WHERE v.id=NEW.voucher_id AND (av.account_id IS NULL OR av.ledger_id<>v.ledger_id OR l.code_snapshot IS NOT av.code OR l.name_snapshot IS NOT av.name OR l.category_snapshot IS NOT av.category OR l.normal_balance_snapshot IS NOT av.normal_balance)) THEN RAISE(ABORT,'Voucher account snapshots must match reviewed stable account') END;
 INSERT INTO voucher_review_completions SELECT id,book_id,NEW.id,content_fingerprint,NEW.created_at FROM vouchers WHERE id=NEW.voucher_id;
END;

ALTER TABLE review_confirmations ADD COLUMN review_reason TEXT NOT NULL DEFAULT '';
CREATE TRIGGER rr_periodic_insert BEFORE INSERT ON periodic_drafts BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NEW.status<>'draft' OR NEW.created_by<>accounting_actor_user() OR NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL OR NEW.posted_voucher_id IS NOT NULL THEN RAISE(ABORT,'Trusted pending periodic preparer required') END;
END;
CREATE TRIGGER rr_periodic_update BEFORE UPDATE ON periodic_drafts BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN OLD.status='draft' AND NEW.status='draft' AND (NEW.description<>OLD.description OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind) AND OLD.created_by<>accounting_actor_user() THEN RAISE(ABORT,'Only periodic preparer may change pending content') END;
 SELECT CASE WHEN OLD.created_by<>NEW.created_by OR OLD.book_id<>NEW.book_id OR OLD.run_id<>NEW.run_id OR NEW.version<OLD.version THEN RAISE(ABORT,'Periodic preparation identity immutable') END;
 SELECT CASE WHEN OLD.status='draft' AND NEW.status='approved' AND NOT COALESCE((NEW.created_by<>accounting_actor_user() AND NEW.approved_by=accounting_actor_user() AND NEW.approved_at IS NOT NULL AND NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.period=OLD.period AND NEW.kind=OLD.kind AND EXISTS(SELECT 1 FROM service_book_user_permissions p WHERE p.book_id=NEW.book_id AND p.user_id=accounting_actor_user() AND p.can_review)),0) THEN RAISE(ABORT,'Independent periodic reviewer required') END;
 SELECT CASE WHEN OLD.status<>'draft' AND NOT COALESCE((OLD.status='approved' AND NEW.status='posted' AND NEW.version=OLD.version AND NEW.description=OLD.description AND NEW.period=OLD.period AND NEW.kind=OLD.kind AND NEW.approved_by=OLD.approved_by AND NEW.approved_at=OLD.approved_at AND EXISTS(SELECT 1 FROM vouchers v JOIN voucher_review_completions c ON c.id=v.id WHERE v.id=NEW.posted_voucher_id AND v.periodic_review_id=OLD.id)),0) THEN RAISE(ABORT,'Periodic approved content immutable') END;
 SELECT CASE WHEN OLD.status='draft' AND NEW.status NOT IN('draft','approved') THEN RAISE(ABORT,'Periodic review required') END;
END;
CREATE TRIGGER rr_periodic_line_insert BEFORE INSERT ON periodic_draft_lines BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM periodic_drafts WHERE id=NEW.draft_id AND status='draft' AND created_by=accounting_actor_user()) THEN RAISE(ABORT,'Periodic reviewed lines immutable') END;
END;
CREATE TRIGGER rr_periodic_line_update BEFORE UPDATE ON periodic_draft_lines BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NEW.draft_id<>OLD.draft_id OR NEW.line_no<>OLD.line_no OR NEW.id<>OLD.id THEN RAISE(ABORT,'Periodic line identity immutable') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM periodic_drafts WHERE id=NEW.draft_id AND status='draft' AND created_by=accounting_actor_user()) THEN RAISE(ABORT,'Periodic reviewed lines immutable') END;
END;
CREATE TRIGGER rr_periodic_line_delete BEFORE DELETE ON periodic_draft_lines BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM periodic_drafts WHERE id=OLD.draft_id AND status='draft' AND created_by=accounting_actor_user()) THEN RAISE(ABORT,'Periodic reviewed lines immutable') END;
END;

CREATE UNIQUE INDEX rr_draft_once ON vouchers(source_id) WHERE source_kind='draft' AND review_completion_id IS NOT NULL;
CREATE TRIGGER rr_periodic_line_insert_revision AFTER INSERT ON periodic_draft_lines BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=NEW.draft_id; END;
CREATE TRIGGER rr_periodic_line_update_revision AFTER UPDATE ON periodic_draft_lines WHEN NEW.draft_id IS NOT OLD.draft_id OR NEW.line_no IS NOT OLD.line_no OR NEW.account_id IS NOT OLD.account_id OR NEW.account_version IS NOT OLD.account_version OR NEW.debit_cents IS NOT OLD.debit_cents OR NEW.credit_cents IS NOT OLD.credit_cents OR NEW.aux_json IS NOT OLD.aux_json OR NEW.source_ref IS NOT OLD.source_ref BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=NEW.draft_id; END;
CREATE TRIGGER rr_periodic_line_delete_revision AFTER DELETE ON periodic_draft_lines BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=OLD.draft_id; END;
CREATE TRIGGER rr_periodic_header_revision AFTER UPDATE ON periodic_drafts WHEN OLD.status='draft' AND NEW.status='draft' AND NEW.version=OLD.version AND (NEW.description<>OLD.description OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind) BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=NEW.id; END;
