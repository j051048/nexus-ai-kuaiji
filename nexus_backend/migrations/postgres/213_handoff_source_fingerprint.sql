-- compatibility: breaking
-- P1: failure results may be empty; use only the frozen batch plan, never a
-- guessed current book. Additional validation complements the existing
-- manager/actor/append-only guard and does not grant any new table privilege.
CREATE OR REPLACE FUNCTION accounting.handoff_source_fingerprint_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE i RECORD; parent RECORD; b RECORD; n RECORD; w RECORD;
        bound_book TEXT; expected_digest TEXT; plan_count INTEGER;
BEGIN
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id THEN
  RAISE EXCEPTION 'Handoff book identity invalid' USING ERRCODE='23514';
 END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
 IF NEW.source_kind='batch_item' THEN
  SELECT * INTO i FROM accounting.batch_operation_items WHERE id=NEW.source_id;
  IF NOT FOUND OR i.firm_id IS DISTINCT FROM NEW.firm_id OR i.company_id IS DISTINCT FROM b.company_id
   OR i.status<>'failed' OR GREATEST(i.attempt,1)<>NEW.source_version THEN
   RAISE EXCEPTION 'Handoff batch identity invalid' USING ERRCODE='23514';
  END IF;
  SELECT * INTO parent FROM accounting.batch_operations WHERE id=i.batch_id;
  IF NOT FOUND OR parent.firm_id IS DISTINCT FROM NEW.firm_id
   OR parent.request_json::jsonb->>'period' IS DISTINCT FROM b.period THEN
   RAISE EXCEPTION 'Handoff batch scope invalid' USING ERRCODE='23514';
  END IF;
  bound_book := i.result_json::jsonb->>'book_id';
  IF parent.kind IN ('review_export','package_draft') THEN
   SELECT count(*),min(entry->>'book_id') INTO plan_count,bound_book
    FROM jsonb_array_elements(parent.request_json::jsonb->'companies') entry
    WHERE entry->>'company_id'=i.company_id;
   IF plan_count<>1 OR (i.result_json::jsonb->>'book_id' IS NOT NULL
     AND i.result_json::jsonb->>'book_id' IS DISTINCT FROM bound_book) THEN
    RAISE EXCEPTION 'Handoff frozen plan invalid' USING ERRCODE='23514';
   END IF;
  END IF;
  expected_digest := encode(sha256(convert_to('batch_item|'||i.id||'|'||i.batch_id||'|'||i.attempt::text||'|'||i.error_code||'|'||i.updated_at,'UTF8')),'hex');
  IF bound_book IS DISTINCT FROM NEW.book_id OR expected_digest IS DISTINCT FROM NEW.source_fingerprint THEN
   RAISE EXCEPTION 'Handoff source binding invalid' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.source_kind='notification_investigation' THEN
  SELECT * INTO n FROM accounting.delivery_notifications WHERE id=NEW.source_id;
  IF NOT FOUND OR n.book_id IS DISTINCT FROM NEW.book_id OR n.firm_id IS DISTINCT FROM NEW.firm_id
   OR n.company_id IS DISTINCT FROM b.company_id OR n.state<>'unknown' THEN
   RAISE EXCEPTION 'Handoff notification identity invalid' USING ERRCODE='23514';
  END IF;
  expected_digest := encode(sha256(convert_to('notification_investigation|'||n.id||'|'||coalesce(n.package_id,'None')||'|'||coalesce(n.package_version::text,'None')||'|'||coalesce(n.manifest_sha256,'None')||'|'||n.attempts::text||'|'||n.updated_at,'UTF8')),'hex');
  IF expected_digest IS DISTINCT FROM NEW.source_fingerprint THEN
   RAISE EXCEPTION 'Handoff notification fingerprint invalid' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.source_kind='tax_workpaper' THEN
  SELECT * INTO w FROM accounting.tax_workpapers WHERE id=NEW.source_id;
  IF NOT FOUND OR EXISTS(SELECT 1 FROM accounting.tax_workpapers newer WHERE newer.book_id=w.book_id AND newer.period=w.period AND newer.version>w.version) THEN
   RAISE EXCEPTION 'Handoff workpaper superseded' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.handoff_source_fingerprint_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS handoff_source_fingerprint_guard ON accounting.handoff_tasks;
CREATE TRIGGER handoff_source_fingerprint_guard BEFORE INSERT ON accounting.handoff_tasks
 FOR EACH ROW EXECUTE FUNCTION accounting.handoff_source_fingerprint_guard();
