-- Fixed case targets cannot silently follow a changed period. Complete original
-- exception/supplement tasks before replacing an unreviewed source. This is an
-- independent guard, so settlement guards on the same lineage table compose.
CREATE FUNCTION accounting.revision_active_case_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF accounting.case_source_blocked(NEW.old_book_id,NEW.old_source_id) THEN
  RAISE EXCEPTION 'Original active exception or supplement must finish before fact replacement; cases do not migrate periods' USING ERRCODE='23514';
 END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.revision_active_case_guard() FROM PUBLIC,accounting_app,accounting_worker;
CREATE TRIGGER revision_active_case_guard BEFORE INSERT ON accounting.fact_revision_lineage FOR EACH ROW EXECUTE FUNCTION accounting.revision_active_case_guard();
