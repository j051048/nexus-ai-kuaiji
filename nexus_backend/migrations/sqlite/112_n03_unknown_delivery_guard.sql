-- compatibility: breaking
-- New task keys, kinds, grant versions or restore epochs cannot turn an
-- uncertain/in-flight delivery of this exact package to this recipient into a retry.
CREATE TRIGGER IF NOT EXISTS delivery_notification_uncertain_guard_ins
BEFORE INSERT ON delivery_notifications
WHEN NEW.kind IN('signature_reminder','signature_overdue')
BEGIN
 SELECT CASE WHEN EXISTS(
   SELECT 1 FROM delivery_notifications n
   WHERE n.package_id=NEW.package_id AND n.recipient_user_id=NEW.recipient_user_id
   AND n.state IN('claimed','sending','unknown'))
  THEN RAISE(ABORT,'Uncertain or in-flight package delivery requires reconciliation') END;
END;
