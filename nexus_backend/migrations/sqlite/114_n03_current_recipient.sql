-- compatibility: breaking
-- A frozen address/grant is evidence, not perpetual access. A reminder is
-- useful only while this exact recipient can open and sign this exact package.
-- Reuse the actual acknowledgement eligibility view, rather than implementing
-- a second interpretation of client roles/book access/current service contact.
CREATE VIEW IF NOT EXISTS delivery_notification_recipient_eligibility AS
SELECT p.id AS package_id,p.firm_id,p.company_id,p.book_id,p.period,
 p.contact_user_id AS recipient_user_id
FROM monthly_delivery_packages p
JOIN monthly_delivery_eligible_contacts c
 ON c.book_id=p.book_id AND c.user_id=p.contact_user_id
WHERE p.created_by<>p.contact_user_id;

CREATE TRIGGER IF NOT EXISTS delivery_notification_recipient_current_ins
BEFORE INSERT ON delivery_notifications
WHEN NEW.kind IN('signature_reminder','signature_overdue')
BEGIN
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM delivery_notification_recipient_eligibility e
   WHERE e.package_id=NEW.package_id AND e.firm_id=NEW.firm_id
   AND e.company_id=NEW.company_id AND e.book_id=NEW.book_id AND e.period=NEW.period
   AND e.recipient_user_id=NEW.recipient_user_id)
  THEN RAISE(ABORT,'Notification recipient must currently be able to open and sign this package') END;
END;

CREATE TRIGGER IF NOT EXISTS delivery_notification_recipient_current_upd
BEFORE UPDATE ON delivery_notifications
WHEN NEW.kind IN('signature_reminder','signature_overdue') AND NEW.state IN('claimed','sending')
BEGIN
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM delivery_notification_recipient_eligibility e
   WHERE e.package_id=NEW.package_id AND e.firm_id=NEW.firm_id
   AND e.company_id=NEW.company_id AND e.book_id=NEW.book_id AND e.period=NEW.period
   AND e.recipient_user_id=NEW.recipient_user_id)
  THEN RAISE(ABORT,'Notification recipient must currently be able to open and sign this package') END;
END;
