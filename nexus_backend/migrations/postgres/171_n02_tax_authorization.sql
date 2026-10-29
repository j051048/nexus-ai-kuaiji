-- compatibility: breaking
-- N02: role/actor/scope backstops for tax workpapers. Source recomputation stays
-- in the service; this column freezes the exact input snapshot for that check.
ALTER TABLE accounting.tax_workpapers
 ADD COLUMN IF NOT EXISTS source_snapshot_json TEXT NOT NULL DEFAULT '';

DROP POLICY IF EXISTS tax_profile_insert ON accounting.tax_profiles;
CREATE POLICY tax_profile_insert ON accounting.tax_profiles FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id)
 AND created_by=accounting.actor_user());
DROP POLICY IF EXISTS tax_workpaper_review ON accounting.tax_workpapers;
CREATE POLICY tax_workpaper_review ON accounting.tax_workpapers FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)
 AND reviewed_by=accounting.actor_user());

CREATE OR REPLACE FUNCTION accounting.n02_tax_scope_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Tax records are append-only' USING ERRCODE='42501';
 END IF;
 PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
 IF NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NOT accounting.actor_active() THEN
  RAISE EXCEPTION 'Tax actor scope is invalid' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='tax_profiles' THEN
  IF TG_OP<>'INSERT' THEN
   RAISE EXCEPTION 'Tax profiles are append-only' USING ERRCODE='42501';
  END IF;
  IF NEW.created_by IS DISTINCT FROM accounting.actor_user()
     OR NOT accounting.company_manager(NEW.company_id) THEN
   RAISE EXCEPTION 'Current company management permission is required' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.companies c
    JOIN accounting.firm_clients fc ON fc.company_id=c.id
    WHERE c.id=NEW.company_id AND c.status='active' AND fc.status='active'
      AND fc.firm_id=NEW.firm_id) THEN
   RAISE EXCEPTION 'Tax profile company scope is invalid' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.created_by IS DISTINCT FROM accounting.actor_user()
     OR NOT accounting.book_allowed(NEW.book_id,true) THEN
   RAISE EXCEPTION 'Current edit permission and maker identity are required' USING ERRCODE='42501';
  END IF;
 ELSE
  IF NOT accounting.book_review_allowed(NEW.book_id)
     OR NEW.reviewed_by IS DISTINCT FROM accounting.actor_user() THEN
   RAISE EXCEPTION 'Current review permission and reviewer identity are required' USING ERRCODE='42501';
  END IF;
  IF NEW.source_snapshot_json IS DISTINCT FROM OLD.source_snapshot_json THEN
   RAISE EXCEPTION 'Tax source snapshot is immutable' USING ERRCODE='23514';
  END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b
   JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
   JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
   JOIN accounting.periods p ON p.book_id=b.id AND p.period=NEW.period
   WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id
     AND b.period=NEW.period AND p.status='open')
    OR EXISTS(SELECT 1 FROM accounting.internal_period_locks l
              WHERE l.book_id=NEW.book_id AND l.period=NEW.period) THEN
  RAISE EXCEPTION 'Tax workpaper book/company/period scope must be current and open' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.tax_profiles p
   WHERE p.id=NEW.profile_id AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id
     AND p.effective_from<=NEW.period
     AND NOT EXISTS(SELECT 1 FROM accounting.tax_profiles newer
       WHERE newer.firm_id=NEW.firm_id AND newer.company_id=NEW.company_id
         AND newer.effective_from>p.effective_from AND newer.effective_from<=NEW.period)) THEN
  RAISE EXCEPTION 'Tax profile must be the effective profile in the same scope' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.n02_tax_scope_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.n02_tax_scope_guard() TO accounting_app;
DROP TRIGGER IF EXISTS zz_n02_tax_scope_guard ON accounting.tax_profiles;
CREATE TRIGGER zz_n02_tax_scope_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_profiles
 FOR EACH ROW EXECUTE FUNCTION accounting.n02_tax_scope_guard();
DROP TRIGGER IF EXISTS zz_n02_tax_scope_guard ON accounting.tax_workpapers;
CREATE TRIGGER zz_n02_tax_scope_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_workpapers
 FOR EACH ROW EXECUTE FUNCTION accounting.n02_tax_scope_guard();
