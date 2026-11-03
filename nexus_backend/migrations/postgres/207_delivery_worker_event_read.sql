-- compatibility: additive
-- NEXT06: the worker that inserts an append-only provider event must read back
-- exactly the stored receipt to report its id; it still cannot update/delete.
DROP POLICY IF EXISTS delivery_provider_events_worker_read ON accounting.delivery_provider_events;
CREATE POLICY delivery_provider_events_worker_read ON accounting.delivery_provider_events
 FOR SELECT TO accounting_worker USING (true);
