-- compatibility: breaking
-- Forward-only repair: signed transport evidence must belong to its notification.
-- Keep 205-207 bytes/checksums unchanged; their unknown labels remain breaking.
SET LOCAL search_path=accounting,pg_catalog;

DO $check$
BEGIN
 IF EXISTS (
  SELECT 1 FROM accounting.delivery_provider_events e
  LEFT JOIN accounting.delivery_notifications n ON n.id=e.notification_id
  WHERE n.id IS NULL OR (e.firm_id,e.company_id,e.book_id)
   IS DISTINCT FROM (n.firm_id,n.company_id,n.book_id)
 ) THEN
  RAISE EXCEPTION 'Provider receipt scope requires investigation' USING ERRCODE='23514';
 END IF;
END $check$;

CREATE OR REPLACE FUNCTION accounting.provider_receipt_scope_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM accounting.delivery_notifications n
  WHERE n.id=NEW.notification_id AND n.firm_id=NEW.firm_id
   AND n.company_id=NEW.company_id AND n.book_id=NEW.book_id
 ) THEN
  RAISE EXCEPTION 'Provider receipt scope mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.provider_receipt_scope_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS provider_receipt_scope_guard ON accounting.delivery_provider_events;
CREATE TRIGGER provider_receipt_scope_guard BEFORE INSERT ON accounting.delivery_provider_events
 FOR EACH ROW EXECUTE FUNCTION accounting.provider_receipt_scope_guard();
