-- compatibility: breaking
-- F03: immutable close evidence plus monotonic revisions, including ABA edits.
ALTER TABLE accounting.internal_close_runs ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '{}';
CREATE TABLE accounting.close_dependency_revisions(
 scope_kind TEXT NOT NULL CHECK(scope_kind IN('book','ledger','template')),
 scope_id TEXT NOT NULL, revision BIGINT NOT NULL CHECK(revision>0),
 PRIMARY KEY(scope_kind,scope_id)
);
ALTER TABLE accounting.close_dependency_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.close_dependency_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY close_dependency_read ON accounting.close_dependency_revisions FOR SELECT TO accounting_app
 USING(accounting.actor_active() AND (
  (scope_kind='book' AND accounting.book_allowed(scope_id)) OR
  (scope_kind='ledger' AND EXISTS(SELECT 1 FROM accounting.ledger_periods p
    WHERE p.ledger_id=scope_id AND accounting.book_allowed(p.book_id))) OR
  scope_kind='template'
 ));
REVOKE ALL ON accounting.close_dependency_revisions FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
GRANT SELECT ON accounting.close_dependency_revisions TO accounting_app;

CREATE FUNCTION accounting.close_dependency_scope(mode TEXT,value JSONB) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE kind TEXT; target TEXT;
BEGIN
 kind:='book';
 CASE mode
  WHEN 'book' THEN target:=value->>'book_id';
  WHEN 'book_self' THEN target:=value->>'id';
  WHEN 'voucher_line' THEN SELECT book_id INTO target FROM accounting.vouchers WHERE id=value->>'voucher_id';
  WHEN 'periodic_line' THEN SELECT book_id INTO target FROM accounting.periodic_drafts WHERE id=value->>'draft_id';
  WHEN 'report_note' THEN SELECT book_id INTO target FROM accounting.report_runs WHERE id=value->>'report_id';
  WHEN 'ledger' THEN kind:='ledger';target:=value->>'ledger_id';
  WHEN 'opening_version' THEN kind:='ledger';
   SELECT ledger_id INTO target FROM accounting.opening_packages WHERE id=value->>'package_id';
  WHEN 'opening_line' THEN kind:='ledger';
   SELECT p.ledger_id INTO target FROM accounting.opening_packages p
    JOIN accounting.opening_package_versions v ON v.package_id=p.id
    WHERE v.id=value->>'package_version_id';
  WHEN 'template' THEN kind:='template';target:=value->>'id';
  ELSE RAISE EXCEPTION 'Unknown close dependency scope' USING ERRCODE='23514';
 END CASE;
 IF target IS NULL OR target='' THEN
  RAISE EXCEPTION 'Close dependency must have a valid parent scope' USING ERRCODE='23514';
 END IF;
 RETURN jsonb_build_object('kind',kind,'id',target);
END $$;
REVOKE ALL ON FUNCTION accounting.close_dependency_scope(TEXT,JSONB) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

CREATE FUNCTION accounting.close_dependency_write_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE scope JSONB; old_scope JSONB; b RECORD; allowed_delivery BOOLEAN:=false;
BEGIN
 scope:=accounting.close_dependency_scope(TG_ARGV[0],CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END);
 IF TG_OP='UPDATE' THEN
  old_scope:=accounting.close_dependency_scope(TG_ARGV[0],to_jsonb(OLD));
  IF old_scope IS DISTINCT FROM scope THEN
   RAISE EXCEPTION 'Close dependency scope cannot be changed' USING ERRCODE='23514';
  END IF;
 END IF;
 IF scope->>'kind'='template' THEN
  -- Template configuration is shared; its rare writes use the existing
  -- exclusive compatibility gate, never a new reversed lock order.
  PERFORM accounting.lock_legacy_write();
 ELSIF scope->>'kind'='book' THEN
  IF TG_TABLE_NAME='books' AND TG_OP='INSERT' THEN
   PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
  ELSE
   SELECT firm_id,company_id INTO b FROM accounting.books WHERE id=scope->>'id';
   PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
  END IF;
  IF TG_TABLE_NAME='drafts' AND TG_OP='UPDATE' THEN
   allowed_delivery:=OLD.status='approved' AND NEW.status='exported'
    AND (to_jsonb(NEW)-'status'-'exported_at')=(to_jsonb(OLD)-'status'-'exported_at');
  ELSIF TG_TABLE_NAME='report_runs' AND TG_OP='UPDATE' THEN
   allowed_delivery:=OLD.status='reviewed' AND NEW.status='published'
    AND (to_jsonb(NEW)-'status'-'published_by'-'published_at')=(to_jsonb(OLD)-'status'-'published_by'-'published_at');
  END IF;
  IF NOT allowed_delivery AND TG_TABLE_NAME NOT IN('books','periods','accounts','ledger_periods')
   AND EXISTS(SELECT 1 FROM accounting.internal_period_locks WHERE book_id=scope->>'id') THEN
   RAISE EXCEPTION 'Internally locked period evidence cannot change' USING ERRCODE='23514';
  END IF;
 ELSE
  SELECT firm_id,company_id INTO b FROM accounting.ledgers WHERE id=scope->>'id';
  PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
REVOKE ALL ON FUNCTION accounting.close_dependency_write_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

CREATE FUNCTION accounting.close_dependency_bump() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE scope JSONB;
BEGIN
 scope:=accounting.close_dependency_scope(TG_ARGV[0],CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END);
 INSERT INTO accounting.close_dependency_revisions(scope_kind,scope_id,revision)
 VALUES(scope->>'kind',scope->>'id',1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=close_dependency_revisions.revision+1;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
REVOKE ALL ON FUNCTION accounting.close_dependency_bump() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.books
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book_self');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.books
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book_self');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.periods
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.periods
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.sources
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.sources
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.source_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.source_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.draft_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.draft_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ledger_periods
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.ledger_periods
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_bindings
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.opening_bindings
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.vouchers
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.vouchers
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.voucher_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('voucher_line');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.voucher_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('voucher_line');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.voucher_sources
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('voucher_line');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.voucher_sources
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('voucher_line');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.subledger_items
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.subledger_items
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.subledger_allocations
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.subledger_allocations
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.subledger_allocation_reversals
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.subledger_allocation_reversals
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.bank_statement_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.bank_statement_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.bank_recon_adjustments
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.bank_recon_adjustments
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.periodic_rules
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.periodic_rules
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.periodic_assets
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.periodic_assets
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.periodic_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.periodic_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.periodic_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.periodic_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.periodic_draft_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('periodic_line');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.periodic_draft_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('periodic_line');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.report_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.report_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.report_notes
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('report_note');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.report_notes
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('report_note');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.accounting_policies
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('ledger');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.accounting_policies
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('ledger');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ledger_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('ledger');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.ledger_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('ledger');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ledger_account_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('ledger');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.ledger_account_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('ledger');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ledger_account_mappings
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('ledger');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.ledger_account_mappings
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('ledger');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_packages
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('ledger');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.opening_packages
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('ledger');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_package_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('opening_version');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.opening_package_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('opening_version');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('opening_line');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.opening_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('opening_line');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_open_items
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('opening_line');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.opening_open_items
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('opening_line');

CREATE TRIGGER p0_close_dependency_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.report_templates
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('template');
CREATE TRIGGER p0_close_dependency_revision AFTER INSERT OR UPDATE OR DELETE ON accounting.report_templates
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('template');

CREATE FUNCTION accounting.close_dependencies_match(target_book TEXT,value TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE vector JSONB; ledger TEXT; kind TEXT; target TEXT; revision BIGINT;
BEGIN
 vector:=value::jsonb;
 SELECT ledger_id INTO ledger FROM accounting.ledger_periods WHERE book_id=target_book;
 IF vector->>'version' IS DISTINCT FROM '1'
  OR vector->'book'->>'id' IS DISTINCT FROM target_book
  OR vector->'ledger'->>'id' IS DISTINCT FROM ledger
  OR coalesce(vector->'template'->>'id','')='' THEN RETURN false; END IF;
 FOREACH kind IN ARRAY ARRAY['book','ledger','template'] LOOP
  target:=vector->kind->>'id';
  SELECT r.revision INTO revision FROM accounting.close_dependency_revisions r
   WHERE r.scope_kind=kind AND r.scope_id=target;
  IF vector->kind->>'revision' IS DISTINCT FROM coalesce(revision,0)::TEXT THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION accounting.close_dependencies_match(TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

CREATE FUNCTION accounting.close_revision_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE run accounting.internal_close_runs; book accounting.books; ledger TEXT;
BEGIN
 SELECT * INTO book FROM accounting.books WHERE id=NEW.book_id;
 PERFORM accounting.lock_company_write(book.firm_id,book.company_id);
 IF TG_TABLE_NAME='internal_period_locks' THEN
  SELECT * INTO run FROM accounting.internal_close_runs WHERE id=NEW.close_run_id;
  SELECT ledger_id INTO ledger FROM accounting.ledger_periods WHERE book_id=NEW.book_id;
  IF NOT accounting.book_review_allowed(NEW.book_id)
   OR run.id IS NULL OR run.book_id<>NEW.book_id OR run.period<>NEW.period OR run.status<>'reviewed'
   OR NEW.firm_id<>book.firm_id OR NEW.company_id<>book.company_id OR NEW.ledger_id<>ledger
   OR run.reviewed_by IS NULL OR run.reviewed_by=run.created_by
   OR NOT accounting.close_dependencies_match(NEW.book_id,run.dependencies_json) THEN
   RAISE EXCEPTION 'Current independently reviewed close dependencies required' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME='internal_close_runs' THEN
  IF (to_jsonb(NEW)-'status'-'reviewed_by'-'reviewed_at'-'locked_by'-'locked_at'-'published_by'-'published_at')
   IS DISTINCT FROM
   (to_jsonb(OLD)-'status'-'reviewed_by'-'reviewed_at'-'locked_by'-'locked_at'-'published_by'-'published_at') THEN
   RAISE EXCEPTION 'Close preflight evidence is immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.status='reviewed' AND OLD.status IS DISTINCT FROM NEW.status AND (
   OLD.status<>'preflight_passed' OR NEW.reviewed_by IS DISTINCT FROM accounting.actor_user()
   OR NEW.created_by=accounting.actor_user()) THEN
   RAISE EXCEPTION 'Independent current close reviewer required' USING ERRCODE='42501';
  END IF;
  IF NEW.status IN('reviewed','locked') AND OLD.status IS DISTINCT FROM NEW.status
   AND NOT accounting.close_dependencies_match(NEW.book_id,NEW.dependencies_json) THEN
   RAISE EXCEPTION 'Close dependencies changed; repeat preflight and review' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.close_revision_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE TRIGGER p0_close_revision_guard BEFORE UPDATE ON accounting.internal_close_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.close_revision_guard();
CREATE TRIGGER p0_close_lock_revision_guard BEFORE INSERT ON accounting.internal_period_locks
 FOR EACH ROW EXECUTE FUNCTION accounting.close_revision_guard();
