-- Client chart and template configuration; no statutory chart is implied.
ALTER TABLE accounting.accounts ADD COLUMN category TEXT NOT NULL DEFAULT 'other' CHECK(category IN ('asset','liability','equity','cost','income','expense','other'));
ALTER TABLE accounting.accounts ADD COLUMN normal_balance TEXT NOT NULL DEFAULT 'debit' CHECK(normal_balance IN ('debit','credit'));
ALTER TABLE accounting.accounts ADD COLUMN auxiliary_requirements TEXT NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(auxiliary_requirements::jsonb)='array' AND auxiliary_requirements::jsonb <@ '["department","counterparty","employee","project"]'::jsonb AND jsonb_array_length(auxiliary_requirements::jsonb)<=4);
ALTER TABLE accounting.accounts ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK(version>0);

CREATE TABLE accounting.journal_templates (
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),active INTEGER NOT NULL CHECK(active IN (0,1)),
 current_version INTEGER NOT NULL CHECK(current_version>0),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(id,book_id)
);
CREATE TABLE accounting.journal_template_versions (
 template_id TEXT NOT NULL,book_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),
 definition TEXT NOT NULL CHECK(jsonb_typeof(definition::jsonb)='object'),definition_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(template_id,version),FOREIGN KEY(template_id,book_id) REFERENCES accounting.journal_templates(id,book_id)
);
CREATE INDEX idx_journal_templates_book ON accounting.journal_templates(book_id,active);
CREATE TABLE accounting.draft_line_auxiliaries (
 draft_id TEXT NOT NULL,book_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
 values_json TEXT NOT NULL CHECK(jsonb_typeof(values_json::jsonb)='object'),
 PRIMARY KEY(draft_id,ordinal),FOREIGN KEY(draft_id,ordinal) REFERENCES accounting.draft_lines(draft_id,ordinal) ON DELETE CASCADE,
 FOREIGN KEY(draft_id,book_id) REFERENCES accounting.drafts(id,book_id)
);

CREATE FUNCTION accounting.catalog_manage(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target,true) AND EXISTS(
  SELECT 1 FROM accounting.books b WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND (
   accounting.firm_manager(b.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.book_access ba WHERE ba.book_id=b.id AND ba.user_id=accounting.actor_user() AND ba.role='reviewer'
    AND (accounting.firm_role(b.firm_id)='reviewer' OR (accounting.firm_role(b.firm_id) IS NULL AND EXISTS(
     SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=b.company_id AND cm.user_id=ba.user_id AND cm.role='finance')))
   )
  )
 )
$$;
CREATE FUNCTION accounting.catalog_aux_valid(target TEXT,dimension TEXT,value TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target) AND length(value) BETWEEN 1 AND 200 AND EXISTS(
  SELECT 1 FROM accounting.books b WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND CASE dimension
   WHEN 'department' THEN EXISTS(SELECT 1 FROM accounting.departments d WHERE d.id=value AND d.company_id=b.company_id)
   WHEN 'employee' THEN EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.user_id=value AND cm.company_id=b.company_id)
   WHEN 'counterparty' THEN true WHEN 'project' THEN true ELSE false END
 )
$$;
CREATE FUNCTION accounting.catalog_mutation_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target TEXT; BEGIN
  target:=CASE WHEN TG_OP='DELETE' THEN OLD.book_id ELSE NEW.book_id END;
  IF NOT accounting.catalog_manage(target) OR NOT EXISTS(
   SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=target AND p.status='open'
  ) THEN RAISE EXCEPTION 'catalog requires current reviewer and open period' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' AND NEW.book_id<>OLD.book_id THEN RAISE EXCEPTION 'catalog identity is immutable'; END IF;
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
 END
$$;
CREATE FUNCTION accounting.template_history_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 RAISE EXCEPTION 'journal template history is immutable'; END $$;
CREATE FUNCTION accounting.catalog_account_update_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.book_id<>OLD.book_id OR NEW.code<>OLD.code THEN RAISE EXCEPTION 'account code and book are immutable'; END IF;
 IF NOT accounting.catalog_manage(OLD.book_id) OR NOT EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=OLD.book_id AND p.status='open'
 ) THEN RAISE EXCEPTION 'catalog requires current reviewer and open period' USING ERRCODE='42501'; END IF;
 IF NEW.auxiliary_requirements::jsonb<>OLD.auxiliary_requirements::jsonb AND EXISTS(
  SELECT 1 FROM accounting.draft_lines WHERE book_id=OLD.book_id AND account_code=OLD.code
 ) THEN RAISE EXCEPTION 'used account auxiliary requirements are immutable'; END IF;
 RETURN NEW; END $$;
CREATE FUNCTION accounting.draft_aux_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target RECORD; BEGIN
  IF TG_OP='DELETE' THEN target:=OLD; ELSE target:=NEW; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.drafts WHERE id=target.draft_id AND book_id=target.book_id AND status='draft')
  THEN RAISE EXCEPTION 'approved auxiliary values are immutable'; END IF;
  IF TG_OP='UPDATE' AND (NEW.draft_id<>OLD.draft_id OR NEW.book_id<>OLD.book_id OR NEW.ordinal<>OLD.ordinal)
  THEN RAISE EXCEPTION 'auxiliary identity is immutable'; END IF;
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
 END
$$;
REVOKE ALL ON FUNCTION accounting.catalog_manage(TEXT),accounting.catalog_aux_valid(TEXT,TEXT,TEXT),
 accounting.catalog_mutation_guard(),accounting.template_history_guard(),accounting.catalog_account_update_guard(),accounting.draft_aux_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.catalog_manage(TEXT),accounting.catalog_aux_valid(TEXT,TEXT,TEXT) TO accounting_app;

CREATE TRIGGER journal_templates_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.journal_templates FOR EACH ROW EXECUTE FUNCTION accounting.catalog_mutation_guard();
CREATE TRIGGER catalog_accounts_update BEFORE UPDATE ON accounting.accounts FOR EACH ROW EXECUTE FUNCTION accounting.catalog_account_update_guard();
CREATE TRIGGER journal_template_versions_guard BEFORE INSERT ON accounting.journal_template_versions FOR EACH ROW EXECUTE FUNCTION accounting.catalog_mutation_guard();
CREATE TRIGGER journal_template_versions_immutable BEFORE UPDATE OR DELETE ON accounting.journal_template_versions FOR EACH ROW EXECUTE FUNCTION accounting.template_history_guard();
CREATE TRIGGER draft_aux_mutation_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.draft_line_auxiliaries FOR EACH ROW EXECUTE FUNCTION accounting.draft_aux_guard();
-- Prior phases allowed editors to write all ledger tables. Publishing client
-- classification rules now requires the same explicit reviewer capability
-- enforced by the API. Book initialization is performed by a firm manager.
DROP POLICY ledger_create ON accounting.accounts;
DROP POLICY ledger_update ON accounting.accounts;
CREATE POLICY ledger_create ON accounting.accounts FOR INSERT TO accounting_app WITH CHECK(accounting.catalog_manage(book_id));
CREATE POLICY ledger_update ON accounting.accounts FOR UPDATE TO accounting_app USING(accounting.catalog_manage(book_id)) WITH CHECK(accounting.catalog_manage(book_id));

ALTER TABLE accounting.journal_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.journal_templates FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.journal_template_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.journal_template_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.draft_line_auxiliaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.draft_line_auxiliaries FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.journal_templates,accounting.journal_template_versions,accounting.draft_line_auxiliaries FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON accounting.journal_templates TO accounting_app;
GRANT SELECT,INSERT ON accounting.journal_template_versions TO accounting_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON accounting.draft_line_auxiliaries TO accounting_app;
CREATE POLICY catalog_templates_read ON accounting.journal_templates FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY catalog_templates_create ON accounting.journal_templates FOR INSERT TO accounting_app WITH CHECK(accounting.catalog_manage(book_id) AND created_by=accounting.actor_user());
CREATE POLICY catalog_templates_update ON accounting.journal_templates FOR UPDATE TO accounting_app USING(accounting.catalog_manage(book_id)) WITH CHECK(accounting.catalog_manage(book_id));
CREATE POLICY catalog_versions_read ON accounting.journal_template_versions FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY catalog_versions_create ON accounting.journal_template_versions FOR INSERT TO accounting_app WITH CHECK(accounting.catalog_manage(book_id) AND created_by=accounting.actor_user());
CREATE POLICY catalog_aux_read ON accounting.draft_line_auxiliaries FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY catalog_aux_write ON accounting.draft_line_auxiliaries FOR ALL TO accounting_app USING(accounting.book_allowed(book_id,true)) WITH CHECK(accounting.book_allowed(book_id,true));
