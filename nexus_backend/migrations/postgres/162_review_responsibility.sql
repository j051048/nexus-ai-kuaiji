-- compatibility: breaking
-- New review facts never upgrade legacy approvals.
SET LOCAL search_path=accounting,pg_catalog;
CREATE OR REPLACE FUNCTION accounting.rr_transaction_id() RETURNS TEXT LANGUAGE sql VOLATILE SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT current_database()||':'||pg_postmaster_start_time()::text||':'||txid_current()::text $$;
REVOKE ALL ON FUNCTION accounting.rr_transaction_id() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
-- Versioned review responsibility. Old facts remain explicitly unverified.
CREATE TABLE ledger_review_policies(ledger_id TEXT NOT NULL REFERENCES ledgers(id),version INTEGER NOT NULL CHECK(version>0),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN('independent','single_operator')),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),confirmed_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(ledger_id,version));
CREATE TABLE draft_preparations(draft_id TEXT PRIMARY KEY REFERENCES drafts(id),book_id TEXT NOT NULL REFERENCES books(id),version INTEGER NOT NULL,maker_user_id TEXT REFERENCES users(id),content_revision INTEGER NOT NULL CHECK(content_revision>0),updated_at TEXT NOT NULL,version_transaction TEXT NOT NULL DEFAULT '');
CREATE TABLE draft_preparation_history(draft_id TEXT NOT NULL REFERENCES drafts(id),content_revision INTEGER NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),version INTEGER NOT NULL,maker_user_id TEXT REFERENCES users(id),changed_at TEXT NOT NULL,transaction_id TEXT NOT NULL DEFAULT '',PRIMARY KEY(draft_id,content_revision));
CREATE TABLE draft_review_receipts(draft_id TEXT NOT NULL REFERENCES drafts(id),reviewed_version INTEGER NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),maker_user_id TEXT NOT NULL REFERENCES users(id),reviewer_user_id TEXT NOT NULL REFERENCES users(id),policy_version INTEGER NOT NULL,mode TEXT NOT NULL CHECK(mode IN('independent','single_operator')),independent INTEGER NOT NULL CHECK(independent IN(0,1)),reason TEXT NOT NULL,reviewed_at TEXT NOT NULL,content_revision INTEGER NOT NULL,content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),PRIMARY KEY(draft_id,reviewed_version),FOREIGN KEY(draft_id,content_revision) REFERENCES draft_preparation_history(draft_id,content_revision),CHECK((mode='independent' AND independent=1 AND maker_user_id<>reviewer_user_id) OR (mode='single_operator' AND independent=0)));
CREATE TABLE manual_review_requests(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),version INTEGER NOT NULL CHECK(version>0),prepared_by TEXT NOT NULL REFERENCES users(id),status TEXT NOT NULL CHECK(status IN('pending','approved')),policy_version INTEGER NOT NULL CHECK(policy_version>=0),content_json TEXT NOT NULL CHECK(jsonb_typeof(content_json::jsonb)='object'),content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),command_key TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(trim(reason))>=3),reviewed_by TEXT REFERENCES users(id),reviewed_at TEXT,independent INTEGER NOT NULL DEFAULT 0 CHECK(independent IN(0,1)),review_reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(book_id,command_key));
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
INSERT INTO draft_preparations SELECT id,book_id,version,NULL,1,clock_timestamp()::text,'' FROM drafts;
INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,'' FROM draft_preparations;

ALTER TABLE review_confirmations ADD COLUMN review_reason TEXT NOT NULL DEFAULT '';

CREATE VIEW rr_book_permissions AS SELECT b.id AS book_id,u.id AS user_id,
 (m.role IN('owner','admin') OR ba.role IN('editor','reviewer') AND (m.role IN('operator','reviewer') OR m.role IS NULL AND cm.role IN('owner','admin','finance'))) AS can_edit,
 (m.role IN('owner','admin') OR ba.role='reviewer' AND (m.role='reviewer' OR m.role IS NULL AND cm.role='finance')) AS can_review
 FROM books b JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN users u ON u.active=1 LEFT JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id;
CREATE VIEW rr_book_policies AS SELECT lp.book_id,lp.ledger_id,COALESCE(p.version,0) AS version,COALESCE(p.mode,'independent') AS mode FROM ledger_periods lp LEFT JOIN ledger_review_policies p ON p.ledger_id=lp.ledger_id AND p.version=(SELECT MAX(x.version) FROM ledger_review_policies x WHERE x.ledger_id=lp.ledger_id);
CREATE VIEW rr_draft_contents AS SELECT d.id AS draft_id,encode(sha256(convert_to(jsonb_build_array(d.book_id,d.source_id,d.description,d.explanation,(SELECT jsonb_agg(jsonb_build_array(l.ordinal,l.account_code,l.debit_cents,l.credit_cents,COALESCE(a.values_json,'{}')::jsonb) ORDER BY l.ordinal) FROM draft_lines l LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal WHERE l.draft_id=d.id))::text,'UTF8')),'hex') AS fingerprint FROM drafts d;
CREATE VIEW rr_expected_voucher_lines AS
 SELECT v.id AS voucher_id,ROW_NUMBER() OVER(PARTITION BY v.id ORDER BY l.ordinal) AS line_no,m.account_id,m.account_version,l.debit_cents,l.credit_cents,COALESCE(a.values_json,'{}') AS aux_json,d.source_id AS source_ref FROM vouchers v JOIN drafts d ON d.id=v.source_id AND d.book_id=v.book_id JOIN draft_lines l ON l.draft_id=d.id JOIN ledger_account_mappings m ON m.book_id=d.book_id AND m.book_account_code=l.account_code LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal WHERE v.source_kind='draft' AND v.periodic_review_id IS NULL
 UNION ALL SELECT v.id,j.ordinality::integer,j.value->>'account_id',(j.value->>'account_version')::integer,((j.value->>'debit')::numeric*100)::bigint,((j.value->>'credit')::numeric*100)::bigint,(j.value->'auxiliary_values')::text,j.value->>'source_ref' FROM vouchers v JOIN manual_review_requests r ON r.id=v.review_request_id CROSS JOIN LATERAL jsonb_array_elements(r.content_json::jsonb->'lines') WITH ORDINALITY AS j(value,ordinality) WHERE v.periodic_review_id IS NULL AND v.source_kind IN('manual','adjustment','reversal')
 UNION ALL SELECT v.id,l.line_no,l.account_id,l.account_version,l.debit_cents,l.credit_cents,l.aux_json,l.source_ref FROM vouchers v JOIN periodic_draft_lines l ON l.draft_id=v.periodic_review_id WHERE v.periodic_review_id IS NOT NULL;
CREATE VIEW rr_verified_voucher_headers AS
 SELECT v.id FROM vouchers v JOIN drafts d ON d.id=v.source_id AND d.book_id=v.book_id JOIN draft_review_receipts r ON r.draft_id=d.id AND r.reviewed_version=CASE WHEN d.status='approved' THEN d.version-1 ELSE d.version-2 END JOIN rr_draft_contents dc ON dc.draft_id=d.id AND dc.fingerprint=r.content_fingerprint
 WHERE v.source_kind='draft' AND v.review_request_id IS NULL AND v.periodic_review_id IS NULL AND d.status IN('approved','exported') AND v.source_version=d.version AND v.relation_kind IS NULL AND v.reversal_of IS NULL AND v.description=d.description AND v.review_mode=r.mode AND v.review_maker_id=r.maker_user_id AND v.review_reviewer_id=r.reviewer_user_id AND v.voucher_review_policy_version=r.policy_version
 UNION ALL SELECT v.id FROM vouchers v JOIN manual_review_requests r ON r.id=v.review_request_id AND r.book_id=v.book_id JOIN ledger_periods lp ON lp.book_id=r.book_id LEFT JOIN ledger_review_policies p ON p.ledger_id=lp.ledger_id AND p.version=r.policy_version
 WHERE v.source_kind IN('manual','adjustment','reversal') AND v.periodic_review_id IS NULL AND v.source_version IS NULL AND v.source_id IS NOT DISTINCT FROM (CASE WHEN v.source_kind='reversal' THEN v.reversal_of ELSE NULL END) AND r.status='approved' AND v.review_mode=COALESCE(p.mode,'independent') AND (r.policy_version=0 OR p.version IS NOT NULL) AND v.review_maker_id=r.prepared_by AND v.review_reviewer_id=r.reviewed_by AND v.voucher_review_policy_version=r.policy_version
 AND v.voucher_date=(r.content_json::jsonb->>'voucher_date') AND v.voucher_type=(r.content_json::jsonb->>'voucher_type') AND v.description=(r.content_json::jsonb->>'description') AND v.relation_kind IS NOT DISTINCT FROM (r.content_json::jsonb->>'relation_kind') AND v.reversal_of IS NOT DISTINCT FROM (r.content_json::jsonb->>'original_voucher_id')
 AND v.source_kind=COALESCE((r.content_json::jsonb->>'relation_kind'),'manual')
 UNION ALL SELECT v.id FROM vouchers v JOIN periodic_drafts d ON d.id=v.periodic_review_id AND d.book_id=v.book_id
 WHERE v.source_kind='manual' AND v.review_request_id IS NULL AND v.source_id IS NULL AND v.source_version IS NULL AND d.status='approved' AND d.created_by<>d.approved_by AND v.review_mode='independent' AND v.review_maker_id=d.created_by AND v.review_reviewer_id=d.approved_by AND v.voucher_review_policy_version=0 AND v.description=d.description AND v.voucher_type='转' AND v.relation_kind IS NULL AND v.reversal_of IS NULL;
CREATE OR REPLACE FUNCTION accounting.rr_policy_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
IF NOT EXISTS(SELECT 1 FROM ledgers l JOIN memberships m ON m.firm_id=l.firm_id AND m.user_id=accounting.actor_user() JOIN users u ON u.id=m.user_id AND u.active=1 JOIN firm_clients fc ON fc.firm_id=l.firm_id AND fc.company_id=l.company_id AND fc.status='active' JOIN companies c ON c.id=l.company_id AND c.status='active' WHERE l.id=NEW.ledger_id AND l.firm_id=NEW.firm_id AND l.company_id=NEW.company_id AND l.firm_id=accounting.actor_firm() AND m.role IN('owner','admin') AND NEW.confirmed_by=accounting.actor_user()) OR NEW.version<>COALESCE((SELECT MAX(version)+1 FROM ledger_review_policies WHERE ledger_id=NEW.ledger_id),1) THEN RAISE EXCEPTION 'Current ledger manager and next policy version required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_policy_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_policy_insert ON accounting.ledger_review_policies;
CREATE TRIGGER rr_policy_insert BEFORE INSERT ON accounting.ledger_review_policies FOR EACH ROW EXECUTE FUNCTION accounting.rr_policy_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_policy_revision_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=close_dependency_revisions.revision+1;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_policy_revision_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_policy_revision ON accounting.ledger_review_policies;
CREATE TRIGGER rr_policy_revision AFTER INSERT ON accounting.ledger_review_policies FOR EACH ROW EXECUTE FUNCTION accounting.rr_policy_revision_fn();

CREATE OR REPLACE FUNCTION accounting.rr_policy_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
RAISE EXCEPTION 'Review policy immutable' USING ERRCODE='23514';
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_policy_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_policy_update ON accounting.ledger_review_policies;
CREATE TRIGGER rr_policy_update BEFORE UPDATE ON accounting.ledger_review_policies FOR EACH ROW EXECUTE FUNCTION accounting.rr_policy_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_policy_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(OLD.firm_id,OLD.company_id);
RAISE EXCEPTION 'Review policy immutable' USING ERRCODE='23514';
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_policy_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_policy_delete ON accounting.ledger_review_policies;
CREATE TRIGGER rr_policy_delete BEFORE DELETE ON accounting.ledger_review_policies FOR EACH ROW EXECUTE FUNCTION accounting.rr_policy_delete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_insert_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
IF NEW.status<>'draft' OR NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL OR NEW.exported_at IS NOT NULL THEN RAISE EXCEPTION 'New draft cannot carry approval' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_insert_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_insert_guard ON accounting.drafts;
CREATE TRIGGER rr_draft_insert_guard BEFORE INSERT ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.rr_draft_insert_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 INSERT INTO draft_preparations VALUES(NEW.id,NEW.book_id,NEW.version,NULLIF(accounting.actor_user(),''),1,clock_timestamp()::text,accounting.rr_transaction_id());
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=NEW.id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_insert ON accounting.drafts;
CREATE TRIGGER rr_draft_insert AFTER INSERT ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.rr_draft_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_update_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
IF NEW.id<>OLD.id OR NEW.book_id<>OLD.book_id OR NEW.source_id<>OLD.source_id OR NEW.version<OLD.version THEN RAISE EXCEPTION 'Draft identity and monotonic version required' USING ERRCODE='23514'; END IF;
 IF OLD.status<>'draft' AND NOT COALESCE((OLD.status='approved' AND NEW.status='exported' AND NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.explanation=OLD.explanation AND NEW.approved_by IS NOT DISTINCT FROM OLD.approved_by AND NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at AND NEW.review_policy_version=OLD.review_policy_version AND NEW.review_reason=OLD.review_reason AND NEW.preparation_claim_reason=OLD.preparation_claim_reason AND NEW.last_manual_edit_version=OLD.last_manual_edit_version AND EXISTS(SELECT 1 FROM draft_review_receipts r JOIN rr_draft_contents c ON c.draft_id=r.draft_id AND c.fingerprint=r.content_fingerprint WHERE r.draft_id=OLD.id AND r.reviewed_version=OLD.version-1)),false) THEN RAISE EXCEPTION 'Reviewed draft content immutable or legacy receipt missing' USING ERRCODE='23514'; END IF;
 IF NEW.preparation_claim_reason<>OLD.preparation_claim_reason AND (OLD.status<>'draft' OR length(trim(NEW.preparation_claim_reason))<3 OR NEW.version<>OLD.version+1 OR NOT EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=OLD.id AND p.maker_user_id IS NULL) OR NOT EXISTS(SELECT 1 FROM rr_book_permissions p WHERE p.book_id=OLD.book_id AND p.user_id=accounting.actor_user() AND p.can_edit)) THEN RAISE EXCEPTION 'Only unattributed pending preparation can be claimed' USING ERRCODE='23514'; END IF;
 IF OLD.status='draft' AND NEW.status='approved' AND NOT COALESCE((
  NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.explanation=OLD.explanation AND NEW.approved_by=accounting.actor_user() AND NEW.approved_at IS NOT NULL
  AND EXISTS(SELECT 1 FROM rr_book_permissions p JOIN books b ON b.id=p.book_id WHERE p.book_id=OLD.book_id AND b.firm_id=accounting.actor_firm() AND p.user_id=accounting.actor_user() AND p.can_review)
  AND EXISTS(SELECT 1 FROM draft_preparations p JOIN rr_book_policies pol ON pol.book_id=p.book_id WHERE p.draft_id=OLD.id AND p.version=OLD.version AND p.maker_user_id IS NOT NULL AND pol.version=NEW.review_policy_version AND ((pol.mode='independent' AND p.maker_user_id<>accounting.actor_user()) OR (pol.mode='single_operator' AND length(trim(NEW.review_reason))>=3)))
  AND NOT EXISTS(SELECT 1 FROM business_mappings bm JOIN business_events e ON e.id=bm.event_id WHERE bm.draft_id=OLD.id AND e.applicant_id=accounting.actor_user())
 ),false) THEN RAISE EXCEPTION 'Current independent reviewer and policy required' USING ERRCODE='23514'; END IF;
 IF OLD.status='draft' AND NEW.status NOT IN('draft','approved') THEN RAISE EXCEPTION 'Approval receipt required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_update_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_update_guard ON accounting.drafts;
CREATE TRIGGER rr_draft_update_guard BEFORE UPDATE ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.rr_draft_update_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 UPDATE draft_preparations SET version=NEW.version,version_transaction=CASE WHEN NEW.version<>OLD.version THEN accounting.rr_transaction_id() ELSE version_transaction END,maker_user_id=CASE WHEN NEW.description<>OLD.description OR NEW.explanation<>OLD.explanation OR NEW.preparation_claim_reason<>OLD.preparation_claim_reason THEN NULLIF(accounting.actor_user(),'') ELSE maker_user_id END,content_revision=content_revision+1,updated_at=clock_timestamp()::text WHERE draft_id=NEW.id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=NEW.id;
 UPDATE drafts SET version=version+1 WHERE id=NEW.id AND NEW.version=OLD.version AND (NEW.description<>OLD.description OR NEW.explanation<>OLD.explanation) AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.id AND p.version_transaction<>accounting.rr_transaction_id());
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_update ON accounting.drafts;
CREATE TRIGGER rr_draft_update AFTER UPDATE ON accounting.drafts FOR EACH ROW WHEN (OLD.status='draft' AND NEW.status='draft' AND (NEW.version<>OLD.version OR NEW.description<>OLD.description OR NEW.explanation<>OLD.explanation OR NEW.preparation_claim_reason<>OLD.preparation_claim_reason)) EXECUTE FUNCTION accounting.rr_draft_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_approve_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 INSERT INTO draft_review_receipts SELECT NEW.id,OLD.version,NEW.book_id,p.maker_user_id,accounting.actor_user(),pol.version,pol.mode,CASE WHEN pol.mode='independent' THEN 1 ELSE 0 END,NEW.review_reason,NEW.approved_at,p.content_revision,c.fingerprint FROM draft_preparations p JOIN rr_book_policies pol ON pol.book_id=p.book_id JOIN rr_draft_contents c ON c.draft_id=p.draft_id WHERE p.draft_id=NEW.id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_approve_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_approve ON accounting.drafts;
CREATE TRIGGER rr_draft_approve AFTER UPDATE ON accounting.drafts FOR EACH ROW WHEN (OLD.status='draft' AND NEW.status='approved') EXECUTE FUNCTION accounting.rr_draft_approve_fn();

CREATE OR REPLACE FUNCTION accounting.rr_line_insert_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
PERFORM 1 FROM accounting.drafts WHERE id=NEW.draft_id FOR UPDATE;
IF NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE EXCEPTION 'Only pending draft content can change' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_line_insert_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_line_insert_guard ON accounting.draft_lines;
CREATE TRIGGER rr_line_insert_guard BEFORE INSERT ON accounting.draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_line_insert_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_line_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting.rr_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting.actor_user(),''),content_revision=content_revision+1,updated_at=clock_timestamp()::text WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_line_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_line_insert ON accounting.draft_lines;
CREATE TRIGGER rr_line_insert AFTER INSERT ON accounting.draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_line_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_line_update_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
PERFORM 1 FROM accounting.drafts WHERE id=NEW.draft_id FOR UPDATE;
IF NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE EXCEPTION 'Only pending draft content can change' USING ERRCODE='23514'; END IF;
 IF NEW.draft_id<>OLD.draft_id OR NEW.book_id<>OLD.book_id OR NEW.ordinal<>OLD.ordinal THEN RAISE EXCEPTION 'Draft line identity immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_line_update_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_line_update_guard ON accounting.draft_lines;
CREATE TRIGGER rr_line_update_guard BEFORE UPDATE ON accounting.draft_lines FOR EACH ROW WHEN (NEW.draft_id IS DISTINCT FROM OLD.draft_id OR NEW.book_id IS DISTINCT FROM OLD.book_id OR NEW.ordinal IS DISTINCT FROM OLD.ordinal OR NEW.account_code IS DISTINCT FROM OLD.account_code OR NEW.debit_cents IS DISTINCT FROM OLD.debit_cents OR NEW.credit_cents IS DISTINCT FROM OLD.credit_cents) EXECUTE FUNCTION accounting.rr_line_update_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_line_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting.rr_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting.actor_user(),''),content_revision=content_revision+1,updated_at=clock_timestamp()::text WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_line_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_line_update ON accounting.draft_lines;
CREATE TRIGGER rr_line_update AFTER UPDATE ON accounting.draft_lines FOR EACH ROW WHEN (NEW.draft_id IS DISTINCT FROM OLD.draft_id OR NEW.book_id IS DISTINCT FROM OLD.book_id OR NEW.ordinal IS DISTINCT FROM OLD.ordinal OR NEW.account_code IS DISTINCT FROM OLD.account_code OR NEW.debit_cents IS DISTINCT FROM OLD.debit_cents OR NEW.credit_cents IS DISTINCT FROM OLD.credit_cents) EXECUTE FUNCTION accounting.rr_line_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_line_delete_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=OLD.book_id;
PERFORM 1 FROM accounting.drafts WHERE id=OLD.draft_id FOR UPDATE;
IF NOT EXISTS(SELECT 1 FROM drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id AND status='draft') THEN RAISE EXCEPTION 'Only pending draft content can change' USING ERRCODE='23514'; END IF;
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_line_delete_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_line_delete_guard ON accounting.draft_lines;
CREATE TRIGGER rr_line_delete_guard BEFORE DELETE ON accounting.draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_line_delete_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_line_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 UPDATE drafts SET version=version+1 WHERE id=OLD.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=OLD.draft_id AND p.version_transaction<>accounting.rr_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting.actor_user(),''),content_revision=content_revision+1,updated_at=clock_timestamp()::text WHERE draft_id=OLD.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=OLD.draft_id;
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_line_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_line_delete ON accounting.draft_lines;
CREATE TRIGGER rr_line_delete AFTER DELETE ON accounting.draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_line_delete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_aux_insert_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
PERFORM 1 FROM accounting.drafts WHERE id=NEW.draft_id FOR UPDATE;
IF NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE EXCEPTION 'Only pending draft content can change' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_aux_insert_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_aux_insert_guard ON accounting.draft_line_auxiliaries;
CREATE TRIGGER rr_aux_insert_guard BEFORE INSERT ON accounting.draft_line_auxiliaries FOR EACH ROW EXECUTE FUNCTION accounting.rr_aux_insert_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_aux_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting.rr_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting.actor_user(),''),content_revision=content_revision+1,updated_at=clock_timestamp()::text WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_aux_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_aux_insert ON accounting.draft_line_auxiliaries;
CREATE TRIGGER rr_aux_insert AFTER INSERT ON accounting.draft_line_auxiliaries FOR EACH ROW EXECUTE FUNCTION accounting.rr_aux_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_aux_update_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
PERFORM 1 FROM accounting.drafts WHERE id=NEW.draft_id FOR UPDATE;
IF NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft') THEN RAISE EXCEPTION 'Only pending draft content can change' USING ERRCODE='23514'; END IF;
 IF NEW.draft_id<>OLD.draft_id OR NEW.book_id<>OLD.book_id OR NEW.ordinal<>OLD.ordinal THEN RAISE EXCEPTION 'Draft line identity immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_aux_update_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_aux_update_guard ON accounting.draft_line_auxiliaries;
CREATE TRIGGER rr_aux_update_guard BEFORE UPDATE ON accounting.draft_line_auxiliaries FOR EACH ROW WHEN (NEW.draft_id IS DISTINCT FROM OLD.draft_id OR NEW.book_id IS DISTINCT FROM OLD.book_id OR NEW.ordinal IS DISTINCT FROM OLD.ordinal OR NEW.values_json IS DISTINCT FROM OLD.values_json) EXECUTE FUNCTION accounting.rr_aux_update_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_aux_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 UPDATE drafts SET version=version+1 WHERE id=NEW.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=NEW.draft_id AND p.version_transaction<>accounting.rr_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting.actor_user(),''),content_revision=content_revision+1,updated_at=clock_timestamp()::text WHERE draft_id=NEW.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=NEW.draft_id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_aux_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_aux_update ON accounting.draft_line_auxiliaries;
CREATE TRIGGER rr_aux_update AFTER UPDATE ON accounting.draft_line_auxiliaries FOR EACH ROW WHEN (NEW.draft_id IS DISTINCT FROM OLD.draft_id OR NEW.book_id IS DISTINCT FROM OLD.book_id OR NEW.ordinal IS DISTINCT FROM OLD.ordinal OR NEW.values_json IS DISTINCT FROM OLD.values_json) EXECUTE FUNCTION accounting.rr_aux_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_aux_delete_guard_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=OLD.book_id;
PERFORM 1 FROM accounting.drafts WHERE id=OLD.draft_id FOR UPDATE;
IF NOT EXISTS(SELECT 1 FROM drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id AND status='draft') THEN RAISE EXCEPTION 'Only pending draft content can change' USING ERRCODE='23514'; END IF;
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_aux_delete_guard_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_aux_delete_guard ON accounting.draft_line_auxiliaries;
CREATE TRIGGER rr_aux_delete_guard BEFORE DELETE ON accounting.draft_line_auxiliaries FOR EACH ROW EXECUTE FUNCTION accounting.rr_aux_delete_guard_fn();

CREATE OR REPLACE FUNCTION accounting.rr_aux_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 UPDATE drafts SET version=version+1 WHERE id=OLD.draft_id AND EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=OLD.draft_id AND p.version_transaction<>accounting.rr_transaction_id());
 UPDATE draft_preparations SET maker_user_id=NULLIF(accounting.actor_user(),''),content_revision=content_revision+1,updated_at=clock_timestamp()::text WHERE draft_id=OLD.draft_id;
 INSERT INTO draft_preparation_history SELECT draft_id,content_revision,book_id,version,maker_user_id,updated_at,accounting.rr_transaction_id() FROM draft_preparations WHERE draft_id=OLD.draft_id;
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_aux_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_aux_delete ON accounting.draft_line_auxiliaries;
CREATE TRIGGER rr_aux_delete AFTER DELETE ON accounting.draft_line_auxiliaries FOR EACH ROW EXECUTE FUNCTION accounting.rr_aux_delete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_preparation_history_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'Review evidence immutable' USING ERRCODE='23514';
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_preparation_history_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_preparation_history_update ON accounting.draft_preparation_history;
CREATE TRIGGER rr_draft_preparation_history_update BEFORE UPDATE ON accounting.draft_preparation_history FOR EACH ROW EXECUTE FUNCTION accounting.rr_draft_preparation_history_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_preparation_history_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'Review evidence immutable' USING ERRCODE='23514';
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_preparation_history_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_preparation_history_delete ON accounting.draft_preparation_history;
CREATE TRIGGER rr_draft_preparation_history_delete BEFORE DELETE ON accounting.draft_preparation_history FOR EACH ROW EXECUTE FUNCTION accounting.rr_draft_preparation_history_delete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_review_receipts_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'Review evidence immutable' USING ERRCODE='23514';
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_review_receipts_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_review_receipts_update ON accounting.draft_review_receipts;
CREATE TRIGGER rr_draft_review_receipts_update BEFORE UPDATE ON accounting.draft_review_receipts FOR EACH ROW EXECUTE FUNCTION accounting.rr_draft_review_receipts_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_draft_review_receipts_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'Review evidence immutable' USING ERRCODE='23514';
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_draft_review_receipts_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_draft_review_receipts_delete ON accounting.draft_review_receipts;
CREATE TRIGGER rr_draft_review_receipts_delete BEFORE DELETE ON accounting.draft_review_receipts FOR EACH ROW EXECUTE FUNCTION accounting.rr_draft_review_receipts_delete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_voucher_review_completions_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'Review evidence immutable' USING ERRCODE='23514';
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_voucher_review_completions_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_voucher_review_completions_update ON accounting.voucher_review_completions;
CREATE TRIGGER rr_voucher_review_completions_update BEFORE UPDATE ON accounting.voucher_review_completions FOR EACH ROW EXECUTE FUNCTION accounting.rr_voucher_review_completions_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_voucher_review_completions_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'Review evidence immutable' USING ERRCODE='23514';
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_voucher_review_completions_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_voucher_review_completions_delete ON accounting.voucher_review_completions;
CREATE TRIGGER rr_voucher_review_completions_delete BEFORE DELETE ON accounting.voucher_review_completions FOR EACH ROW EXECUTE FUNCTION accounting.rr_voucher_review_completions_delete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_manual_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN

 IF NOT COALESCE((jsonb_typeof(NEW.content_json::jsonb->'voucher_date')='string' AND (NEW.content_json::jsonb->>'voucher_date') ~ '^20[0-9]{2}-(0[1-9]|1[0-2])-[0-9]{2}$' AND substr(NEW.content_json::jsonb->>'voucher_date',1,7)=(SELECT period FROM books WHERE id=NEW.book_id) AND NEW.content_json::jsonb->>'voucher_type' IN('记','收','付','转') AND jsonb_typeof(NEW.content_json::jsonb->'description')='string' AND length(trim(NEW.content_json::jsonb->>'description')) BETWEEN 1 AND 500 AND NEW.content_json::jsonb->>'reason'=NEW.reason AND jsonb_typeof(NEW.content_json::jsonb->'relation_kind') IN('null','string') AND jsonb_typeof(NEW.content_json::jsonb->'original_voucher_id') IN('null','string')
 AND ((NEW.content_json::jsonb->>'relation_kind' IS NULL AND NEW.content_json::jsonb->>'original_voucher_id' IS NULL) OR (NEW.content_json::jsonb->>'relation_kind' IN('adjustment','reversal') AND EXISTS(SELECT 1 FROM vouchers WHERE id=NEW.content_json::jsonb->>'original_voucher_id' AND book_id=NEW.book_id)))
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.content_json::jsonb->'lines') j(value) WHERE NOT COALESCE((jsonb_typeof(j.value->'account_id')='string' AND jsonb_typeof(j.value->'account_version')='number' AND (j.value->>'account_version')::integer>0 AND jsonb_typeof(j.value->'debit')='string' AND jsonb_typeof(j.value->'credit')='string' AND j.value->>'debit' ~ '^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$' AND j.value->>'credit' ~ '^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$' AND jsonb_typeof(j.value->'auxiliary_values')='object' AND jsonb_typeof(j.value->'source_ref')='string' AND (((j.value->>'debit')::numeric>0 AND (j.value->>'credit')::numeric=0) OR ((j.value->>'credit')::numeric>0 AND (j.value->>'debit')::numeric=0))),false))
 AND (SELECT SUM((j.value->>'debit')::numeric-(j.value->>'credit')::numeric) FROM jsonb_array_elements(NEW.content_json::jsonb->'lines') j(value))=0),false) THEN RAISE EXCEPTION 'Complete balanced manual payload required' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
IF NOT COALESCE((NEW.status='pending' AND NEW.version=1 AND NEW.prepared_by=accounting.actor_user() AND NEW.reviewed_by IS NULL AND NEW.reviewed_at IS NULL AND NEW.independent=0
 AND EXISTS(SELECT 1 FROM rr_book_permissions p JOIN books b ON b.id=p.book_id WHERE p.book_id=NEW.book_id AND b.firm_id=accounting.actor_firm() AND p.user_id=accounting.actor_user() AND p.can_edit AND EXISTS(SELECT 1 FROM periods pr WHERE pr.book_id=b.id AND pr.status='open') AND NOT EXISTS(SELECT 1 FROM internal_period_locks lk WHERE lk.book_id=b.id))
 AND EXISTS(SELECT 1 FROM rr_book_policies p WHERE p.book_id=NEW.book_id AND p.version=NEW.policy_version)
 AND NEW.content_fingerprint=encode(sha256(convert_to(NEW.content_json,'UTF8')),'hex') AND jsonb_typeof(NEW.content_json::jsonb)='object' AND (NEW.content_json::jsonb->>'book_id')=NEW.book_id AND (NEW.content_json::jsonb->>'command_key')=NEW.command_key
 AND jsonb_typeof(NEW.content_json::jsonb->'lines')='array' AND jsonb_array_length(NEW.content_json::jsonb->'lines')>=2 AND jsonb_typeof(NEW.content_json::jsonb->'review_request_id') IS NULL
 ),false) THEN RAISE EXCEPTION 'Trusted preparer and complete current manual request required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_manual_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_manual_insert ON accounting.manual_review_requests;
CREATE TRIGGER rr_manual_insert BEFORE INSERT ON accounting.manual_review_requests FOR EACH ROW EXECUTE FUNCTION accounting.rr_manual_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_manual_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
IF NOT COALESCE((OLD.status='pending' AND NEW.status='approved' AND NEW.version=OLD.version+1 AND NEW.id=OLD.id AND NEW.book_id=OLD.book_id AND NEW.prepared_by=OLD.prepared_by AND NEW.policy_version=OLD.policy_version AND NEW.content_json=OLD.content_json AND NEW.content_fingerprint=OLD.content_fingerprint AND NEW.command_key=OLD.command_key AND NEW.reason=OLD.reason AND NEW.created_at=OLD.created_at AND NEW.reviewed_by=accounting.actor_user() AND NEW.reviewed_at IS NOT NULL
 AND EXISTS(SELECT 1 FROM rr_book_permissions p JOIN books b ON b.id=p.book_id WHERE p.book_id=NEW.book_id AND b.firm_id=accounting.actor_firm() AND p.user_id=accounting.actor_user() AND p.can_review AND EXISTS(SELECT 1 FROM periods pr WHERE pr.book_id=b.id AND pr.status='open') AND NOT EXISTS(SELECT 1 FROM internal_period_locks lk WHERE lk.book_id=b.id))
 AND EXISTS(SELECT 1 FROM rr_book_policies p WHERE p.book_id=OLD.book_id AND p.version=OLD.policy_version AND ((p.mode='independent' AND NEW.independent=1 AND NEW.reviewed_by<>OLD.prepared_by) OR (p.mode='single_operator' AND NEW.independent=0 AND length(trim(NEW.review_reason))>=3)))
 ),false) THEN RAISE EXCEPTION 'Current independent manual review required; approved content immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_manual_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_manual_update ON accounting.manual_review_requests;
CREATE TRIGGER rr_manual_update BEFORE UPDATE ON accounting.manual_review_requests FOR EACH ROW EXECUTE FUNCTION accounting.rr_manual_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_manual_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=OLD.book_id;
RAISE EXCEPTION 'Manual review evidence immutable' USING ERRCODE='23514';
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_manual_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_manual_delete ON accounting.manual_review_requests;
CREATE TRIGGER rr_manual_delete BEFORE DELETE ON accounting.manual_review_requests FOR EACH ROW EXECUTE FUNCTION accounting.rr_manual_delete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_voucher_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
IF NEW.review_completion_id IS DISTINCT FROM NEW.id OR NEW.review_mode NOT IN('independent','single_operator') OR NEW.posted_by<>accounting.actor_user() OR NOT EXISTS(SELECT 1 FROM rr_book_permissions p JOIN books b ON b.id=p.book_id JOIN ledger_periods lp ON lp.book_id=b.id WHERE p.book_id=NEW.book_id AND p.user_id=accounting.actor_user() AND p.can_review AND EXISTS(SELECT 1 FROM periods pr WHERE pr.book_id=b.id AND pr.status='open') AND NOT EXISTS(SELECT 1 FROM internal_period_locks lk WHERE lk.book_id=b.id) AND b.firm_id=accounting.actor_firm() AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND b.period=NEW.period AND substr(NEW.voucher_date,1,7)=b.period AND lp.ledger_id=NEW.ledger_id AND EXISTS(SELECT 1 FROM opening_bindings ob WHERE ob.id=NEW.opening_binding_id AND ob.book_id=b.id AND ob.ledger_id=lp.ledger_id AND ob.firm_id=b.firm_id AND ob.company_id=b.company_id AND ob.period=b.period AND ob.status='active')) THEN RAISE EXCEPTION 'Current reviewer and deferred review completion required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_voucher_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_voucher_insert ON accounting.vouchers;
CREATE TRIGGER rr_voucher_insert BEFORE INSERT ON accounting.vouchers FOR EACH ROW EXECUTE FUNCTION accounting.rr_voucher_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_voucher_line_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM voucher_review_completions WHERE id=NEW.voucher_id) OR EXISTS(SELECT 1 FROM vouchers WHERE id=NEW.voucher_id AND review_completion_id IS NULL) THEN RAISE EXCEPTION 'Completed or legacy voucher lines immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_voucher_line_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_voucher_line_insert ON accounting.voucher_lines;
CREATE TRIGGER rr_voucher_line_insert BEFORE INSERT ON accounting.voucher_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_voucher_line_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_posting_complete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
IF NOT EXISTS(SELECT 1 FROM vouchers v JOIN rr_verified_voucher_headers h ON h.id=v.id WHERE v.id=NEW.voucher_id AND v.command_id=NEW.id AND v.book_id=NEW.book_id AND v.firm_id=NEW.firm_id AND v.company_id=NEW.company_id AND v.posted_by=NEW.created_by AND NEW.created_by=accounting.actor_user() AND v.review_completion_id=v.id AND (v.review_request_id IS NULL OR EXISTS(SELECT 1 FROM manual_review_requests r WHERE r.id=v.review_request_id AND r.command_key=NEW.command_key))) THEN RAISE EXCEPTION 'Voucher header must match frozen review' USING ERRCODE='23514'; END IF;
 IF (SELECT COUNT(*) FROM rr_expected_voucher_lines WHERE voucher_id=NEW.voucher_id)<2 OR (SELECT COUNT(*) FROM voucher_lines WHERE voucher_id=NEW.voucher_id)<>(SELECT COUNT(*) FROM rr_expected_voucher_lines WHERE voucher_id=NEW.voucher_id) OR EXISTS(SELECT 1 FROM rr_expected_voucher_lines e LEFT JOIN voucher_lines l ON l.voucher_id=e.voucher_id AND l.line_no=e.line_no WHERE e.voucher_id=NEW.voucher_id AND (l.id IS NULL OR l.account_id IS DISTINCT FROM e.account_id OR l.account_version IS DISTINCT FROM e.account_version OR l.debit_cents IS DISTINCT FROM e.debit_cents OR l.credit_cents IS DISTINCT FROM e.credit_cents OR l.aux_json::jsonb IS DISTINCT FROM e.aux_json::jsonb OR l.source_ref IS DISTINCT FROM e.source_ref)) OR (SELECT SUM(debit_cents-credit_cents) FROM voucher_lines WHERE voucher_id=NEW.voucher_id)<>0 THEN RAISE EXCEPTION 'Voucher lines must exactly match frozen review' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM vouchers v WHERE v.id=NEW.voucher_id AND v.source_kind='reversal' AND ((SELECT COUNT(*) FROM voucher_lines WHERE voucher_id=v.id)<>(SELECT COUNT(*) FROM voucher_lines WHERE voucher_id=v.reversal_of) OR NOT EXISTS(SELECT 1 FROM vouchers o WHERE o.id=v.reversal_of AND o.book_id=v.book_id) OR EXISTS(SELECT 1 FROM voucher_lines l LEFT JOIN voucher_lines o ON o.voucher_id=v.reversal_of AND o.line_no=l.line_no WHERE l.voucher_id=v.id AND (o.id IS NULL OR l.account_id<>o.account_id OR l.account_version<>o.account_version OR l.debit_cents<>o.credit_cents OR l.credit_cents<>o.debit_cents OR l.aux_json::jsonb<>o.aux_json::jsonb)))) THEN RAISE EXCEPTION 'Reversal must exactly reverse reviewed original' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id LEFT JOIN ledger_account_versions av ON av.account_id=l.account_id AND av.version=l.account_version WHERE v.id=NEW.voucher_id AND (av.account_id IS NULL OR av.ledger_id<>v.ledger_id OR l.code_snapshot IS DISTINCT FROM av.code OR l.name_snapshot IS DISTINCT FROM av.name OR l.category_snapshot IS DISTINCT FROM av.category OR l.normal_balance_snapshot IS DISTINCT FROM av.normal_balance)) THEN RAISE EXCEPTION 'Voucher account snapshots must match reviewed stable account' USING ERRCODE='23514'; END IF;
 INSERT INTO voucher_review_completions SELECT id,book_id,NEW.id,content_fingerprint,NEW.created_at FROM vouchers WHERE id=NEW.voucher_id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_posting_complete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_posting_complete ON accounting.posting_commands;
CREATE TRIGGER rr_posting_complete AFTER INSERT ON accounting.posting_commands FOR EACH ROW EXECUTE FUNCTION accounting.rr_posting_complete_fn();

CREATE OR REPLACE FUNCTION accounting.rr_periodic_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
IF NEW.status<>'draft' OR NEW.created_by<>accounting.actor_user() OR NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL OR NEW.posted_voucher_id IS NOT NULL THEN RAISE EXCEPTION 'Trusted pending periodic preparer required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_insert ON accounting.periodic_drafts;
CREATE TRIGGER rr_periodic_insert BEFORE INSERT ON accounting.periodic_drafts FOR EACH ROW EXECUTE FUNCTION accounting.rr_periodic_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_periodic_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF OLD.status='draft' AND NEW.status='draft' AND (NEW.description<>OLD.description OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind) AND OLD.created_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Only periodic preparer may change pending content' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
IF OLD.created_by<>NEW.created_by OR OLD.book_id<>NEW.book_id OR OLD.run_id<>NEW.run_id OR NEW.version<OLD.version THEN RAISE EXCEPTION 'Periodic preparation identity immutable' USING ERRCODE='23514'; END IF;
 IF OLD.status='draft' AND NEW.status='approved' AND NOT COALESCE((NEW.created_by<>accounting.actor_user() AND NEW.approved_by=accounting.actor_user() AND NEW.approved_at IS NOT NULL AND NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.period=OLD.period AND NEW.kind=OLD.kind AND EXISTS(SELECT 1 FROM rr_book_permissions p WHERE p.book_id=NEW.book_id AND p.user_id=accounting.actor_user() AND p.can_review)),false) THEN RAISE EXCEPTION 'Independent periodic reviewer required' USING ERRCODE='23514'; END IF;
 IF OLD.status<>'draft' AND NOT COALESCE((OLD.status='approved' AND NEW.status='posted' AND NEW.version=OLD.version AND NEW.description=OLD.description AND NEW.period=OLD.period AND NEW.kind=OLD.kind AND NEW.approved_by=OLD.approved_by AND NEW.approved_at=OLD.approved_at AND EXISTS(SELECT 1 FROM vouchers v JOIN voucher_review_completions c ON c.id=v.id WHERE v.id=NEW.posted_voucher_id AND v.periodic_review_id=OLD.id)),false) THEN RAISE EXCEPTION 'Periodic approved content immutable' USING ERRCODE='23514'; END IF;
 IF OLD.status='draft' AND NEW.status NOT IN('draft','approved') THEN RAISE EXCEPTION 'Periodic review required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_update ON accounting.periodic_drafts;
CREATE TRIGGER rr_periodic_update BEFORE UPDATE ON accounting.periodic_drafts FOR EACH ROW EXECUTE FUNCTION accounting.rr_periodic_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_periodic_line_insert_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM periodic_drafts WHERE id=NEW.draft_id AND status='draft' AND created_by=accounting.actor_user()) THEN RAISE EXCEPTION 'Periodic reviewed lines immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_line_insert_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_line_insert ON accounting.periodic_draft_lines;
CREATE TRIGGER rr_periodic_line_insert BEFORE INSERT ON accounting.periodic_draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_periodic_line_insert_fn();

CREATE OR REPLACE FUNCTION accounting.rr_periodic_line_update_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NEW.draft_id<>OLD.draft_id OR NEW.line_no<>OLD.line_no OR NEW.id<>OLD.id THEN RAISE EXCEPTION 'Periodic line identity immutable' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM periodic_drafts WHERE id=NEW.draft_id AND status='draft' AND created_by=accounting.actor_user()) THEN RAISE EXCEPTION 'Periodic reviewed lines immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_line_update_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_line_update ON accounting.periodic_draft_lines;
CREATE TRIGGER rr_periodic_line_update BEFORE UPDATE ON accounting.periodic_draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_periodic_line_update_fn();

CREATE OR REPLACE FUNCTION accounting.rr_periodic_line_delete_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM periodic_drafts WHERE id=OLD.draft_id AND status='draft' AND created_by=accounting.actor_user()) THEN RAISE EXCEPTION 'Periodic reviewed lines immutable' USING ERRCODE='23514'; END IF;
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_line_delete_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_line_delete ON accounting.periodic_draft_lines;
CREATE TRIGGER rr_periodic_line_delete BEFORE DELETE ON accounting.periodic_draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_periodic_line_delete_fn();
ALTER TABLE accounting.ledger_review_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_review_policies FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.ledger_review_policies FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE POLICY rr_read ON accounting.ledger_review_policies FOR SELECT TO accounting_app USING(EXISTS(SELECT 1 FROM accounting.ledger_periods lp WHERE lp.ledger_id=ledger_review_policies.ledger_id AND accounting.book_allowed(lp.book_id)));
GRANT SELECT ON accounting.ledger_review_policies TO accounting_app;
CREATE POLICY rr_insert ON accounting.ledger_review_policies FOR INSERT TO accounting_app WITH CHECK(accounting.firm_manager(firm_id) AND accounting.company_visible(company_id));
GRANT INSERT ON accounting.ledger_review_policies TO accounting_app;
ALTER TABLE accounting.manual_review_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.manual_review_requests FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.manual_review_requests FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE POLICY rr_read ON accounting.manual_review_requests FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
GRANT SELECT ON accounting.manual_review_requests TO accounting_app;
CREATE POLICY rr_insert ON accounting.manual_review_requests FOR INSERT TO accounting_app WITH CHECK(accounting.book_allowed(book_id,true));
CREATE POLICY rr_update ON accounting.manual_review_requests FOR UPDATE TO accounting_app USING(accounting.book_review_allowed(book_id)) WITH CHECK(accounting.book_review_allowed(book_id));
GRANT INSERT,UPDATE ON accounting.manual_review_requests TO accounting_app;
ALTER TABLE accounting.draft_preparations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.draft_preparations FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.draft_preparations FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE POLICY rr_read ON accounting.draft_preparations FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
GRANT SELECT ON accounting.draft_preparations TO accounting_app;
ALTER TABLE accounting.draft_preparation_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.draft_preparation_history FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.draft_preparation_history FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE POLICY rr_read ON accounting.draft_preparation_history FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
GRANT SELECT ON accounting.draft_preparation_history TO accounting_app;
ALTER TABLE accounting.draft_review_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.draft_review_receipts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.draft_review_receipts FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE POLICY rr_read ON accounting.draft_review_receipts FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
GRANT SELECT ON accounting.draft_review_receipts TO accounting_app;
ALTER TABLE accounting.voucher_review_completions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_review_completions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.voucher_review_completions FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE POLICY rr_read ON accounting.voucher_review_completions FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
GRANT SELECT ON accounting.voucher_review_completions TO accounting_app;
REVOKE ALL ON accounting.rr_book_permissions FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
REVOKE ALL ON accounting.rr_book_policies FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
REVOKE ALL ON accounting.rr_draft_contents FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
REVOKE ALL ON accounting.rr_expected_voucher_lines FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
REVOKE ALL ON accounting.rr_verified_voucher_headers FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

CREATE OR REPLACE FUNCTION accounting.admin_company_onboarding(target TEXT,month TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF month IS NULL OR month !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' THEN RAISE EXCEPTION 'Invalid setup month' USING ERRCODE='22023'; END IF;
 IF NOT COALESCE((accounting.actor_active() AND accounting.admin_company_manager(target)
 AND accounting.company_visible(target) AND EXISTS(SELECT 1 FROM accounting.companies c
 JOIN accounting.firm_clients fc ON fc.company_id=c.id AND fc.firm_id=accounting.actor_firm()
 WHERE c.id=target AND c.status='active' AND fc.status='active')),false) THEN RAISE EXCEPTION 'Company setup access denied' USING ERRCODE='42501'; END IF;
 RETURN (SELECT to_jsonb(facts) FROM (
WITH args AS (SELECT target AS company_id,accounting.actor_firm() AS firm_id,month AS period),
 client AS (
   SELECT c.id,COALESCE(p.mode,'department') AS mode,COALESCE(p.version,0) AS policy_version
   FROM accounting.companies c JOIN args a ON a.company_id=c.id
   LEFT JOIN accounting.business_submission_policies p ON p.company_id=c.id AND p.firm_id=a.firm_id
 ), people AS (
   SELECT cm.* FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id AND u.active=1
   JOIN args a ON a.company_id=cm.company_id
 ), month_books AS (
   SELECT b.* FROM accounting.books b JOIN args a ON a.company_id=b.company_id AND a.firm_id=b.firm_id AND a.period=b.period
 ), capabilities AS (
   SELECT b.id AS book_id,u.id AS user_id,
    CASE WHEN fm.role IN ('owner','admin') OR (ba.role IN ('editor','reviewer')
      AND (fm.role IN ('operator','reviewer') OR (fm.role IS NULL AND cm.role IN ('owner','admin','finance')))) THEN 1 ELSE 0 END AS can_edit,
    CASE WHEN fm.role IN ('owner','admin') OR (ba.role='reviewer'
      AND (fm.role='reviewer' OR (fm.role IS NULL AND cm.role='finance'))) THEN 1 ELSE 0 END AS can_review
   FROM month_books b JOIN accounting.users u ON u.active=1
   LEFT JOIN accounting.memberships fm ON fm.firm_id=b.firm_id AND fm.user_id=u.id
   LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
   LEFT JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 ), submitters AS (
   SELECT p.* FROM people p CROSS JOIN client c
   WHERE c.mode='department' OR p.role IN ('owner','admin','finance')
 )
 SELECT c.mode,c.policy_version,
  (SELECT COUNT(*) FROM people) AS active_members,
  (SELECT COUNT(*) FROM submitters) AS submitters,
  (SELECT COUNT(*) FROM month_books) AS books,
  (SELECT COUNT(*) FROM month_books b JOIN accounting.rr_book_policies rp ON rp.book_id=b.id WHERE EXISTS(SELECT 1 FROM capabilities x JOIN capabilities y ON y.book_id=x.book_id WHERE x.book_id=b.id AND x.can_edit=1 AND y.can_review=1 AND (rp.mode='single_operator' OR x.user_id<>y.user_id))) AS staffed_books,
  (SELECT COUNT(*) FROM month_books b JOIN accounting.rr_book_policies rp ON rp.book_id=b.id WHERE rp.mode='single_operator') AS single_operator_books,
  (SELECT COUNT(*) FROM submitters s WHERE
    (c.mode='direct' AND NOT EXISTS(SELECT 1 FROM capabilities x WHERE x.can_edit=1 AND x.user_id<>s.user_id))
    OR (c.mode='department' AND NOT EXISTS(
       SELECT 1 FROM accounting.departments d JOIN args a ON a.company_id=d.company_id
       WHERE (d.id=s.department_id OR (s.department_id IS NULL AND s.role<>'employee'))
        AND EXISTS(SELECT 1 FROM accounting.department_approvers da JOIN people ap ON ap.user_id=da.user_id
          WHERE da.firm_id=a.firm_id AND da.company_id=a.company_id AND da.department_id=d.id AND da.user_id<>s.user_id)
        AND EXISTS(SELECT 1 FROM people fp WHERE fp.role='finance' AND fp.user_id<>s.user_id)
    ))) AS blocked_submitters,
  (SELECT COUNT(*) FROM accounting.business_events e JOIN args a ON a.company_id=e.company_id AND a.firm_id=e.firm_id
    JOIN accounting.business_mappings bm ON bm.event_id=e.id AND bm.firm_id=e.firm_id AND bm.company_id=e.company_id
    JOIN month_books b ON b.id=bm.book_id
    JOIN accounting.drafts dr ON dr.id=bm.draft_id AND dr.book_id=b.id
    WHERE e.status='handed_off' AND dr.status='exported') AS verified_flow,
  COALESCE((SELECT enabled FROM accounting.ai_company_settings s JOIN args a ON a.firm_id=s.firm_id AND a.company_id=s.company_id),0) AS ai_enabled
 FROM client c
) facts);
 END $$;

DROP TRIGGER IF EXISTS rr_manual_dependency_gate ON accounting.manual_review_requests;
CREATE TRIGGER rr_manual_dependency_gate BEFORE INSERT OR UPDATE ON accounting.manual_review_requests FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
DROP TRIGGER IF EXISTS rr_manual_dependency_revision ON accounting.manual_review_requests;
CREATE TRIGGER rr_manual_dependency_revision AFTER INSERT OR UPDATE ON accounting.manual_review_requests FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE UNIQUE INDEX rr_draft_once ON accounting.vouchers(source_id) WHERE source_kind='draft' AND review_completion_id IS NOT NULL;
CREATE OR REPLACE FUNCTION accounting.rr_periodic_line_insert_revision_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=NEW.draft_id; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_line_insert_revision_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_line_insert_revision ON accounting.periodic_draft_lines;
CREATE TRIGGER rr_periodic_line_insert_revision AFTER INSERT ON accounting.periodic_draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_periodic_line_insert_revision_fn();
CREATE OR REPLACE FUNCTION accounting.rr_periodic_line_update_revision_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=NEW.draft_id; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_line_update_revision_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_line_update_revision ON accounting.periodic_draft_lines;
CREATE TRIGGER rr_periodic_line_update_revision AFTER UPDATE ON accounting.periodic_draft_lines FOR EACH ROW WHEN (NEW.draft_id IS DISTINCT FROM OLD.draft_id OR NEW.line_no IS DISTINCT FROM OLD.line_no OR NEW.account_id IS DISTINCT FROM OLD.account_id OR NEW.account_version IS DISTINCT FROM OLD.account_version OR NEW.debit_cents IS DISTINCT FROM OLD.debit_cents OR NEW.credit_cents IS DISTINCT FROM OLD.credit_cents OR NEW.aux_json IS DISTINCT FROM OLD.aux_json OR NEW.source_ref IS DISTINCT FROM OLD.source_ref) EXECUTE FUNCTION accounting.rr_periodic_line_update_revision_fn();
CREATE OR REPLACE FUNCTION accounting.rr_periodic_line_delete_revision_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=OLD.draft_id; RETURN OLD; END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_line_delete_revision_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_line_delete_revision ON accounting.periodic_draft_lines;
CREATE TRIGGER rr_periodic_line_delete_revision AFTER DELETE ON accounting.periodic_draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.rr_periodic_line_delete_revision_fn();
CREATE OR REPLACE FUNCTION accounting.rr_periodic_header_revision_fn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN UPDATE periodic_drafts SET version=version+1 WHERE id=NEW.id; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.rr_periodic_header_revision_fn() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS rr_periodic_header_revision ON accounting.periodic_drafts;
CREATE TRIGGER rr_periodic_header_revision AFTER UPDATE ON accounting.periodic_drafts FOR EACH ROW WHEN (OLD.status='draft' AND NEW.status='draft' AND NEW.version=OLD.version AND (NEW.description<>OLD.description OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind)) EXECUTE FUNCTION accounting.rr_periodic_header_revision_fn();
