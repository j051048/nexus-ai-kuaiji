CREATE TABLE accounting.bank_source_dispositions (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,book_id TEXT NOT NULL,company_id TEXT NOT NULL,
 source_id TEXT NOT NULL UNIQUE,canonical_source_id TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64),canonical_fingerprint TEXT NOT NULL CHECK(length(canonical_fingerprint)=64),
 expected_version INTEGER NOT NULL CHECK(expected_version>=1),reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 reviewed_by TEXT NOT NULL REFERENCES accounting.users(id),idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,
 CHECK(source_id<>canonical_source_id),UNIQUE(firm_id,reviewed_by,idempotency_key),
 FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id),
 FOREIGN KEY(canonical_source_id,book_id) REFERENCES accounting.sources(id,book_id),FOREIGN KEY(book_id,firm_id) REFERENCES accounting.books(id,firm_id)
);
CREATE INDEX idx_bank_dispositions_canonical ON accounting.bank_source_dispositions(book_id,canonical_source_id);
ALTER TABLE accounting.bank_source_dispositions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_source_dispositions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.bank_source_dispositions FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT ON accounting.bank_source_dispositions TO accounting_app;
CREATE POLICY bank_disposition_read ON accounting.bank_source_dispositions FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE TRIGGER bank_disposition_immutable BEFORE UPDATE OR DELETE ON accounting.bank_source_dispositions FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();

CREATE FUNCTION accounting.source_inactive(target_book TEXT,target_source TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book) THEN
   RAISE EXCEPTION 'Source disposition scope denied' USING ERRCODE='42501';
  END IF;
  RETURN accounting.revision_source_inactive(target_book,target_source)
   OR EXISTS(SELECT 1 FROM accounting.bank_source_dispositions WHERE source_id=target_source AND book_id=target_book);
 END $$;
REVOKE ALL ON FUNCTION accounting.source_inactive(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.source_inactive(TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.bank_disposition_truth() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.actor_active() OR NEW.reviewed_by<>accounting.actor_user() OR NEW.firm_id<>accounting.actor_firm()
   OR NOT accounting.ai_book_reviewer(NEW.book_id) THEN RAISE EXCEPTION 'Current book reviewer required' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS(
   SELECT 1 FROM accounting.sources s JOIN accounting.sources c ON c.id=NEW.canonical_source_id AND c.book_id=s.book_id
    JOIN accounting.books b ON b.id=s.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
    JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id
    LEFT JOIN accounting.bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id
    LEFT JOIN accounting.bank_import_keys kc ON kc.source_id=c.id AND kc.book_id=c.book_id
    WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id
     AND p.status='open' AND s.source_kind='bank' AND c.source_kind='bank'
     AND s.fingerprint=NEW.source_fingerprint AND c.fingerprint=NEW.canonical_fingerprint
     AND d.status='draft' AND d.exported_at IS NULL AND d.version=NEW.expected_version+1
     AND s.date=c.date AND s.amount_cents=c.amount_cents AND s.direction=c.direction AND s.counterparty=c.counterparty
     AND (k.source_id IS NOT NULL OR kc.source_id IS NOT NULL) AND (k.source_id IS NULL OR kc.source_id IS NULL OR k.bank_account=kc.bank_account)
     AND NOT EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE bank_source_id=s.id)
     AND NOT EXISTS(SELECT 1 FROM accounting.bank_source_dispositions x WHERE x.source_id IN(s.id,c.id) OR x.canonical_source_id=s.id)
     AND NOT EXISTS(SELECT 1 FROM accounting.bank_duplicate_decisions x WHERE x.source_id=least(s.id,c.id) AND x.other_source_id=greatest(s.id,c.id))
  ) THEN RAISE EXCEPTION 'Current canonical same-transaction facts and unlinked unreviewed duplicate required' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END $$;
REVOKE ALL ON FUNCTION accounting.bank_disposition_truth() FROM PUBLIC;
CREATE TRIGGER bank_disposition_truth BEFORE INSERT ON accounting.bank_source_dispositions FOR EACH ROW EXECUTE FUNCTION accounting.bank_disposition_truth();

CREATE FUNCTION accounting.confirm_same_bank_transaction(target_book TEXT,target_source TEXT,target_canonical TEXT,target_version INTEGER,
 target_source_fingerprint TEXT,target_canonical_fingerprint TEXT,target_reason TEXT,target_key TEXT,target_fingerprint TEXT) RETURNS TEXT
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE prior accounting.bank_source_dispositions; facts RECORD; source_draft RECORD; result_id TEXT; stamp TEXT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  IF pg_has_role(session_user,'accounting_worker','MEMBER') OR NOT accounting.actor_active() OR NOT accounting.ai_book_reviewer(target_book)
   OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book)
   OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_canonical AND book_id=target_book) THEN
   RAISE EXCEPTION 'Current book reviewer and canonical scope required' USING ERRCODE='42501'; END IF;
  IF target_version IS NULL OR target_version<1 OR target_source IS NULL OR target_canonical IS NULL OR target_source=target_canonical
   OR target_source_fingerprint IS NULL OR target_source_fingerprint!~'^[a-f0-9]{64}$'
   OR target_canonical_fingerprint IS NULL OR target_canonical_fingerprint!~'^[a-f0-9]{64}$'
   OR target_fingerprint IS NULL OR target_fingerprint!~'^[a-f0-9]{64}$'
   OR target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 3 AND 1000 OR target_reason<>btrim(target_reason)
   OR target_key IS NULL OR length(target_key) NOT BETWEEN 8 AND 120 OR target_key<>btrim(target_key) THEN
   RAISE EXCEPTION 'Bounded same-transaction command required' USING ERRCODE='23514'; END IF;
  SELECT * INTO prior FROM accounting.bank_source_dispositions WHERE firm_id=accounting.actor_firm() AND reviewed_by=accounting.actor_user() AND idempotency_key=target_key;
  IF FOUND THEN
   IF prior.book_id<>target_book OR prior.source_id<>target_source OR prior.canonical_source_id<>target_canonical OR prior.expected_version<>target_version
    OR prior.source_fingerprint<>target_source_fingerprint OR prior.canonical_fingerprint<>target_canonical_fingerprint
    OR prior.reason<>target_reason OR prior.request_fingerprint<>target_fingerprint THEN
    RAISE EXCEPTION 'Disposition key already binds another command' USING ERRCODE='23514'; END IF;
   RETURN prior.id;
  END IF;
  SELECT b.firm_id,b.company_id INTO facts FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
   WHERE b.id=target_book AND b.firm_id=accounting.actor_firm() AND p.status='open';
  IF NOT FOUND THEN RAISE EXCEPTION 'Open period required' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM accounting.bank_source_dispositions WHERE source_id IN(target_source,target_canonical) OR canonical_source_id=target_source) THEN
   RAISE EXCEPTION 'Isolation cannot reverse or chain existing dispositions' USING ERRCODE='23514'; END IF;
  SELECT id,version,status,exported_at INTO source_draft FROM accounting.drafts WHERE source_id=target_source AND book_id=target_book FOR UPDATE;
  IF NOT FOUND OR source_draft.status<>'draft' OR source_draft.exported_at IS NOT NULL OR source_draft.version<>target_version
   OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE bank_source_id=target_source) THEN
   RAISE EXCEPTION 'Current unlinked unreviewed unexported duplicate required' USING ERRCODE='23514'; END IF;
  stamp:=accounting.ocr_utc_now(); result_id:=md5(clock_timestamp()::TEXT||random()::TEXT||target_source||accounting.actor_user());
  UPDATE accounting.drafts SET version=version+1 WHERE id=source_draft.id AND book_id=target_book;
  INSERT INTO accounting.bank_source_dispositions(id,firm_id,book_id,company_id,source_id,canonical_source_id,source_fingerprint,canonical_fingerprint,
   expected_version,reason,reviewed_by,idempotency_key,request_fingerprint,created_at)
   VALUES(result_id,facts.firm_id,target_book,facts.company_id,target_source,target_canonical,target_source_fingerprint,target_canonical_fingerprint,
    target_version,target_reason,accounting.actor_user(),target_key,target_fingerprint,stamp);
  INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
   VALUES(facts.firm_id,target_book,accounting.actor_user(),'bank.duplicate.isolate',target_source,
    jsonb_build_object('canonical_source_id',target_canonical,'reason',target_reason,'disposition_id',result_id)::TEXT,stamp);
  RETURN result_id;
 END $$;
REVOKE ALL ON FUNCTION accounting.confirm_same_bank_transaction(TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.confirm_same_bank_transaction(TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.isolated_bank_workflow_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_source TEXT; target_book TEXT;
 BEGIN
  IF TG_TABLE_NAME='drafts' THEN
   IF TG_OP='INSERT' THEN target_source:=NEW.source_id; target_book:=NEW.book_id;
   ELSE target_source:=OLD.source_id; target_book:=OLD.book_id; END IF;
  ELSIF TG_TABLE_NAME='draft_lines' THEN
   IF TG_OP='DELETE' THEN SELECT source_id,book_id INTO target_source,target_book FROM accounting.drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id;
   ELSE SELECT source_id,book_id INTO target_source,target_book FROM accounting.drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id; END IF;
   IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM accounting.drafts d JOIN accounting.bank_source_dispositions x ON x.source_id=d.source_id AND x.book_id=d.book_id WHERE d.id=OLD.draft_id AND d.book_id=OLD.book_id) THEN
    RAISE EXCEPTION 'Isolated duplicate lines are frozen' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='ai_bank_links' THEN target_source:=NEW.bank_source_id; target_book:=NEW.book_id;
  ELSIF TG_TABLE_NAME='bank_duplicate_decisions' THEN
   IF EXISTS(SELECT 1 FROM accounting.bank_source_dispositions WHERE book_id=NEW.book_id AND source_id IN(NEW.source_id,NEW.other_source_id)) THEN
    RAISE EXCEPTION 'Isolated duplicate cannot be distinguished' USING ERRCODE='23514'; END IF;
   RETURN NEW;
  END IF;
  IF EXISTS(SELECT 1 FROM accounting.bank_source_dispositions WHERE source_id=target_source AND book_id=target_book) THEN
   RAISE EXCEPTION 'Isolated duplicate cannot execute accounting actions' USING ERRCODE='23514'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
 END $$;
REVOKE ALL ON FUNCTION accounting.isolated_bank_workflow_guard() FROM PUBLIC;
CREATE TRIGGER isolated_bank_draft BEFORE INSERT OR UPDATE OR DELETE ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.isolated_bank_workflow_guard();
CREATE TRIGGER isolated_bank_lines BEFORE INSERT OR UPDATE OR DELETE ON accounting.draft_lines FOR EACH ROW EXECUTE FUNCTION accounting.isolated_bank_workflow_guard();
CREATE TRIGGER isolated_bank_link BEFORE INSERT ON accounting.ai_bank_links FOR EACH ROW EXECUTE FUNCTION accounting.isolated_bank_workflow_guard();
CREATE TRIGGER isolated_bank_distinct BEFORE INSERT ON accounting.bank_duplicate_decisions FOR EACH ROW EXECUTE FUNCTION accounting.isolated_bank_workflow_guard();

CREATE OR REPLACE FUNCTION accounting.bank_source_import_blocked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book) THEN
  RAISE EXCEPTION 'Bank source scope denied' USING ERRCODE='42501'; END IF;
 RETURN accounting.source_inactive(target_book,target_source) OR EXISTS(
  SELECT 1 FROM accounting.sources s JOIN accounting.sources o ON o.book_id=s.book_id AND o.id<>s.id AND o.source_kind='bank'
   LEFT JOIN accounting.bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id
   LEFT JOIN accounting.bank_import_keys ko ON ko.source_id=o.id AND ko.book_id=o.book_id
  WHERE s.id=target_source AND s.book_id=target_book AND s.source_kind='bank'
   AND s.date=o.date AND s.direction=o.direction AND s.amount_cents=o.amount_cents AND s.counterparty=o.counterparty
   AND (k.source_id IS NOT NULL OR ko.source_id IS NOT NULL) AND (k.source_id IS NULL OR ko.source_id IS NULL OR k.bank_account=ko.bank_account)
   AND NOT EXISTS(SELECT 1 FROM accounting.bank_source_dispositions x WHERE x.book_id=target_book AND x.source_id=o.id)
   AND NOT EXISTS(SELECT 1 FROM accounting.bank_duplicate_decisions d WHERE d.book_id=target_book AND d.source_id=least(s.id,o.id) AND d.other_source_id=greatest(s.id,o.id)));
 END $$;
CREATE OR REPLACE FUNCTION accounting.source_workflow_blocked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book) THEN
  RAISE EXCEPTION 'Source workflow scope denied' USING ERRCODE='42501'; END IF;
 RETURN accounting.revision_source_blocked(target_book,target_source) OR accounting.source_inactive(target_book,target_source)
  OR accounting.case_source_blocked(target_book,target_source) OR accounting.bank_source_import_blocked(target_book,target_source)
  OR EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.ai_bank_links l ON l.event_id=m.event_id AND l.book_id=m.book_id
   WHERE m.book_id=target_book AND m.source_id=target_source AND (accounting.revision_source_blocked(target_book,l.bank_source_id)
    OR accounting.source_inactive(target_book,l.bank_source_id) OR accounting.case_source_blocked(target_book,l.bank_source_id)
    OR accounting.bank_source_import_blocked(target_book,l.bank_source_id)));
 END $$;
