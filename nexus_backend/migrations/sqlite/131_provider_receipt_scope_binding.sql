-- compatibility: breaking
-- Existing external evidence is never rewritten or silently reassigned.
CREATE TEMP TABLE receipt_scope_validation(valid INTEGER CHECK(valid=1));
INSERT INTO receipt_scope_validation(valid)
 SELECT CASE WHEN EXISTS (
  SELECT 1 FROM delivery_provider_events e LEFT JOIN delivery_notifications n
   ON n.id=e.notification_id
  WHERE n.id IS NULL OR e.firm_id<>n.firm_id OR e.company_id<>n.company_id OR e.book_id<>n.book_id
 ) THEN 0 ELSE 1 END;
DROP TABLE receipt_scope_validation;
CREATE TRIGGER IF NOT EXISTS provider_receipt_scope_guard
BEFORE INSERT ON delivery_provider_events
WHEN NOT EXISTS (
 SELECT 1 FROM delivery_notifications n WHERE n.id=NEW.notification_id
 AND n.firm_id=NEW.firm_id AND n.company_id=NEW.company_id AND n.book_id=NEW.book_id
)
BEGIN
 SELECT RAISE(ABORT,'Provider receipt scope mismatch');
END;
