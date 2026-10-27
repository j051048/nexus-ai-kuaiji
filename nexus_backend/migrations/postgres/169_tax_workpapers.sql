-- compatibility: incremental
-- Tax workpapers: deterministic VAT preparation, variance evidence and
-- independent review. Nothing here posts a voucher, changes account 2221 or
-- produces a filing; it records only what a person prepared and another checked.
CREATE TABLE IF NOT EXISTS accounting.tax_profiles(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 effective_from TEXT NOT NULL CHECK(effective_from ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 taxpayer_type TEXT NOT NULL CHECK(taxpayer_type IN('small_scale','general')),
 vat_rate_bp INTEGER NOT NULL CHECK(vat_rate_bp BETWEEN 0 AND 10000),
 surtax_rate_bp INTEGER NOT NULL DEFAULT 0 CHECK(surtax_rate_bp BETWEEN 0 AND 10000),
 filing_cycle TEXT NOT NULL DEFAULT 'monthly' CHECK(filing_cycle IN('monthly','quarterly')),
 reason TEXT NOT NULL CHECK(char_length(reason) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 UNIQUE(company_id,effective_from)
);
CREATE INDEX IF NOT EXISTS idx_tax_profiles_company
 ON accounting.tax_profiles(firm_id,company_id,effective_from DESC);

CREATE TABLE IF NOT EXISTS accounting.tax_workpapers(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 period TEXT NOT NULL CHECK(period ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL CHECK(status IN('draft','reviewed')),
 profile_id TEXT NOT NULL REFERENCES accounting.tax_profiles(id),
 output_net_cents INTEGER NOT NULL CHECK(output_net_cents>=0),
 output_tax_cents INTEGER NOT NULL CHECK(output_tax_cents>=0),
 input_net_cents INTEGER NOT NULL CHECK(input_net_cents>=0),
 input_tax_cents INTEGER NOT NULL CHECK(input_tax_cents>=0),
 tax_cents INTEGER NOT NULL CHECK(tax_cents>=0),
 surtax_cents INTEGER NOT NULL CHECK(surtax_cents>=0),
 credit_carry_cents INTEGER NOT NULL DEFAULT 0 CHECK(credit_carry_cents>=0),
 ledger_cents INTEGER NOT NULL,
 variance_cents INTEGER NOT NULL,
 variance_reason TEXT NOT NULL DEFAULT '',
 lines_json TEXT NOT NULL,
 source_digest TEXT NOT NULL CHECK(length(source_digest)=64),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 reviewed_by TEXT REFERENCES accounting.users(id),
 reviewed_at TEXT,
 review_reason TEXT NOT NULL DEFAULT '',
 UNIQUE(book_id,period,version)
);
CREATE INDEX IF NOT EXISTS idx_tax_workpapers_book
 ON accounting.tax_workpapers(firm_id,book_id,period,version DESC);

ALTER TABLE accounting.tax_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_workpapers ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_workpapers FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.tax_profiles FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
REVOKE ALL ON accounting.tax_workpapers FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.tax_profiles TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.tax_workpapers TO accounting_app;

CREATE POLICY tax_profile_read ON accounting.tax_profiles FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.company_visible(company_id));
CREATE POLICY tax_profile_insert ON accounting.tax_profiles FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_visible(company_id)
 AND created_by=accounting.actor_user());
CREATE POLICY tax_workpaper_read ON accounting.tax_workpapers FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY tax_workpaper_insert ON accounting.tax_workpapers FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id,true)
 AND created_by=accounting.actor_user());
CREATE POLICY tax_workpaper_review ON accounting.tax_workpapers FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id,true))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id,true)
 AND reviewed_by=accounting.actor_user());

-- One row-level gate for both tax tables: the company lock comes first, then the
-- workpaper immutability rules. Inserts are gated too; previously only UPDATE was.
CREATE OR REPLACE FUNCTION accounting.tax_workpaper_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE row_json JSONB;
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Tax records are append-only' USING ERRCODE='42501';
 END IF;
 row_json:=to_jsonb(NEW);
 PERFORM accounting.lock_company_write(row_json->>'firm_id',row_json->>'company_id');
 IF TG_OP='INSERT' OR TG_TABLE_NAME<>'tax_workpapers' THEN
  RETURN NEW;
 END IF;
 IF OLD.status<>'draft' OR NEW.status<>'reviewed' THEN
  RAISE EXCEPTION 'Only a draft workpaper can move to reviewed' USING ERRCODE='23514';
 END IF;
 IF NEW.output_net_cents<>OLD.output_net_cents OR NEW.output_tax_cents<>OLD.output_tax_cents
 OR NEW.input_net_cents<>OLD.input_net_cents OR NEW.input_tax_cents<>OLD.input_tax_cents
 OR NEW.tax_cents<>OLD.tax_cents OR NEW.surtax_cents<>OLD.surtax_cents
 OR NEW.credit_carry_cents<>OLD.credit_carry_cents OR NEW.ledger_cents<>OLD.ledger_cents
 OR NEW.variance_cents<>OLD.variance_cents OR NEW.lines_json<>OLD.lines_json
 OR NEW.source_digest<>OLD.source_digest OR NEW.profile_id<>OLD.profile_id
 OR NEW.version<>OLD.version OR NEW.period<>OLD.period OR NEW.book_id<>OLD.book_id THEN
  RAISE EXCEPTION 'Reviewed workpaper numbers are immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.reviewed_by=OLD.created_by THEN
  RAISE EXCEPTION 'Applicant cannot review own tax workpaper' USING ERRCODE='42501';
 END IF;
 IF NEW.variance_cents<>0 AND NEW.variance_reason='' THEN
  RAISE EXCEPTION 'Variance requires an explanation' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.tax_workpaper_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.tax_workpaper_guard() TO accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS tax_profile_write_guard ON accounting.tax_profiles;
CREATE TRIGGER tax_profile_write_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_profiles
 FOR EACH ROW EXECUTE FUNCTION accounting.tax_workpaper_guard();
DROP TRIGGER IF EXISTS tax_workpaper_write_guard ON accounting.tax_workpapers;
CREATE TRIGGER tax_workpaper_write_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_workpapers
 FOR EACH ROW EXECUTE FUNCTION accounting.tax_workpaper_guard();
