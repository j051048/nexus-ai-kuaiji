-- PL/pgSQL expressions must not mention fields belonging to another trigger
-- table, even behind an AND condition. Dispatch on shape before field access.
CREATE OR REPLACE FUNCTION accounting.revision_old_draft_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_draft TEXT;target_book TEXT; BEGIN
 IF TG_TABLE_NAME='drafts' THEN
  target_draft:=OLD.id;target_book:=OLD.book_id;
  IF TG_OP='UPDATE' THEN
   IF NEW.status IN('approved','exported') AND EXISTS(SELECT 1 FROM accounting.fact_revisions
    WHERE old_draft_id=target_draft AND old_book_id=target_book AND state='pending') THEN
    RAISE EXCEPTION 'Pending fact revision requires independent review first' USING ERRCODE='23514'; END IF;
  END IF;
 ELSIF TG_TABLE_NAME='draft_lines' THEN
  IF TG_OP='DELETE' THEN target_draft:=OLD.draft_id;target_book:=OLD.book_id;
  ELSE target_draft:=NEW.draft_id;target_book:=NEW.book_id; END IF;
  IF TG_OP='UPDATE' THEN
   IF EXISTS(SELECT 1 FROM accounting.fact_revision_lineage WHERE old_draft_id=OLD.draft_id AND old_book_id=OLD.book_id) THEN
    RAISE EXCEPTION 'Superseded draft lines cannot move to another draft' USING ERRCODE='23514'; END IF;
  END IF;
 ELSE RAISE EXCEPTION 'Unsupported revision guard table' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM accounting.fact_revision_lineage WHERE old_draft_id=target_draft AND old_book_id=target_book) THEN
  RAISE EXCEPTION 'Superseded draft and original lines remain frozen' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
 END $$;

CREATE OR REPLACE FUNCTION accounting.isolated_bank_workflow_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_source TEXT;target_book TEXT; BEGIN
 IF TG_TABLE_NAME='drafts' THEN
  IF TG_OP='INSERT' THEN target_source:=NEW.source_id;target_book:=NEW.book_id;
  ELSE target_source:=OLD.source_id;target_book:=OLD.book_id; END IF;
 ELSIF TG_TABLE_NAME='draft_lines' THEN
  IF TG_OP='DELETE' THEN
   SELECT source_id,book_id INTO target_source,target_book FROM accounting.drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id;
  ELSE
   SELECT source_id,book_id INTO target_source,target_book FROM accounting.drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id;
  END IF;
  IF TG_OP='UPDATE' THEN
   IF EXISTS(SELECT 1 FROM accounting.drafts d JOIN accounting.bank_source_dispositions x ON x.source_id=d.source_id AND x.book_id=d.book_id
    WHERE d.id=OLD.draft_id AND d.book_id=OLD.book_id) THEN
    RAISE EXCEPTION 'Isolated duplicate lines are frozen' USING ERRCODE='23514'; END IF;
  END IF;
 ELSIF TG_TABLE_NAME='ai_bank_links' THEN target_source:=NEW.bank_source_id;target_book:=NEW.book_id;
 ELSIF TG_TABLE_NAME='bank_duplicate_decisions' THEN
  IF EXISTS(SELECT 1 FROM accounting.bank_source_dispositions WHERE book_id=NEW.book_id AND source_id IN(NEW.source_id,NEW.other_source_id)) THEN
   RAISE EXCEPTION 'Isolated duplicate cannot be distinguished' USING ERRCODE='23514'; END IF; RETURN NEW;
 ELSE RAISE EXCEPTION 'Unsupported isolation guard table' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM accounting.bank_source_dispositions WHERE source_id=target_source AND book_id=target_book) THEN
  RAISE EXCEPTION 'Isolated duplicate cannot execute accounting actions' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
 END $$;

CREATE OR REPLACE FUNCTION accounting.ops_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF (NEW.firm_id,NEW.company_id) IS DISTINCT FROM (OLD.firm_id,OLD.company_id) THEN
  RAISE EXCEPTION 'Operations scope is immutable' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='ops_alerts' THEN
  IF NEW.id IS DISTINCT FROM OLD.id THEN RAISE EXCEPTION 'Alert identity is immutable' USING ERRCODE='23514'; END IF;
 END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.revision_old_draft_guard(),accounting.isolated_bank_workflow_guard(),accounting.ops_scope_guard()
 FROM PUBLIC,accounting_app,accounting_worker;
