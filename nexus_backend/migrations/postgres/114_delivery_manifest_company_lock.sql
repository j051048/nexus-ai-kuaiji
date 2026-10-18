-- compatibility: incremental
-- capture_delivery_manifest is SECURITY DEFINER but entered the legacy global
-- lock inside a declared company transaction. Resolve the export's book first,
-- then take the shared compatibility gate and the company key explicitly.
DO $do$ DECLARE d TEXT; BEGIN
 d:=pg_get_functiondef('accounting.capture_delivery_manifest(text,text,text)'::regprocedure);
 d:=replace(d,'PERFORM accounting.lock_legacy_write();','');
 d:=replace(d,
  'SELECT * INTO book FROM accounting.books WHERE id=exported.book_id AND firm_id=accounting.actor_firm();',
  'SELECT * INTO book FROM accounting.books WHERE id=exported.book_id AND firm_id=accounting.actor_firm();
  PERFORM pg_advisory_xact_lock_shared(6420031786281903101);
  PERFORM pg_advisory_xact_lock(accounting.company_lock_key(book.firm_id,book.company_id));');
 EXECUTE d;
END $do$;
