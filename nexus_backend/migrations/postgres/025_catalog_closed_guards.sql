-- Append-only strengthening after 021. Catalog and auxiliary writes must not
-- reopen a closed accounting period through direct restricted SQL.
CREATE OR REPLACE FUNCTION accounting.draft_aux_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target RECORD; BEGIN
  IF TG_OP='DELETE' THEN target:=OLD; ELSE target:=NEW; END IF;
  IF NOT EXISTS(
   SELECT 1 FROM accounting.drafts d JOIN accounting.books b ON b.id=d.book_id
   JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
   WHERE d.id=target.draft_id AND d.book_id=target.book_id AND d.status='draft' AND p.status='open'
  ) THEN RAISE EXCEPTION 'auxiliary writes require draft and open period'; END IF;
  IF TG_OP='UPDATE' AND (NEW.draft_id<>OLD.draft_id OR NEW.book_id<>OLD.book_id OR NEW.ordinal<>OLD.ordinal)
  THEN RAISE EXCEPTION 'auxiliary identity is immutable'; END IF;
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
 END
$$;
CREATE FUNCTION accounting.catalog_account_insert_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.catalog_manage(NEW.book_id) OR NOT EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
  WHERE b.id=NEW.book_id AND p.status='open'
 ) THEN RAISE EXCEPTION 'catalog requires current reviewer and open period' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.catalog_account_insert_guard() FROM PUBLIC;
CREATE TRIGGER catalog_accounts_insert BEFORE INSERT ON accounting.accounts FOR EACH ROW EXECUTE FUNCTION accounting.catalog_account_insert_guard();
