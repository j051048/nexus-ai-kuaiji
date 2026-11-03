-- compatibility: additive
-- NEXT06: the trusted delivery worker records signed provider events outside the
-- user request. It needs to resolve the notification envelope, and only those
-- four tenant columns; it must never read recipient payloads or write the row.
GRANT SELECT (id, firm_id, company_id, book_id)
 ON accounting.delivery_notifications TO accounting_worker;
DROP POLICY IF EXISTS delivery_notifications_worker_read ON accounting.delivery_notifications;
CREATE POLICY delivery_notifications_worker_read ON accounting.delivery_notifications
 FOR SELECT TO accounting_worker USING (true);
