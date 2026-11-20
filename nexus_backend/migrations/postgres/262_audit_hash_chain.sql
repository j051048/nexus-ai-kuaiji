-- compatibility: breaking
-- OPT03-B: per-firm append-only audit hash chain.
ALTER TABLE accounting.audit_events ADD COLUMN prev_hash TEXT;
ALTER TABLE accounting.audit_events ADD COLUMN row_hash TEXT;

-- The append-only row trigger from 261 blocks the migration backfill. It is
-- recreated below after every historical row has a chain position.
DROP TRIGGER IF EXISTS audit_events_append_only_row ON accounting.audit_events;

CREATE OR REPLACE FUNCTION accounting.audit_event_hash(
 prev text, event_id bigint, firm text, book text, company text, actor text,
 action text, object text, details text, created text
) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT encode(
  sha256(convert_to(
   jsonb_build_array(prev,event_id,firm,book,company,actor,action,object,details,created)::text,
   'UTF8'
  )),
  'hex'
 )
$$;
REVOKE ALL ON FUNCTION accounting.audit_event_hash(text,bigint,text,text,text,text,text,text,text,text) FROM PUBLIC;

DO $$
DECLARE
 r record;
 last_firm text := NULL;
 prev text := '';
BEGIN
 FOR r IN SELECT * FROM accounting.audit_events ORDER BY firm_id,id LOOP
  IF last_firm IS DISTINCT FROM r.firm_id THEN
   prev := '';
   last_firm := r.firm_id;
  END IF;
  UPDATE accounting.audit_events
   SET prev_hash=prev,
       row_hash=accounting.audit_event_hash(
        prev,r.id,r.firm_id,r.book_id,r.company_id,r.user_id,r.action,r.object_id,r.details,r.created_at)
   WHERE id=r.id;
  SELECT row_hash INTO prev FROM accounting.audit_events WHERE id=r.id;
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION accounting.audit_events_chain_guard()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtext('accounting.audit_events:'||NEW.firm_id));
 SELECT COALESCE(row_hash,'') INTO NEW.prev_hash
  FROM accounting.audit_events
  WHERE firm_id=NEW.firm_id AND id<NEW.id
  ORDER BY id DESC LIMIT 1;
 NEW.prev_hash := COALESCE(NEW.prev_hash,'');
 NEW.row_hash := accounting.audit_event_hash(
  NEW.prev_hash,NEW.id,NEW.firm_id,NEW.book_id,NEW.company_id,NEW.user_id,
  NEW.action,NEW.object_id,NEW.details,NEW.created_at);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.audit_events_chain_guard() FROM PUBLIC;

DROP TRIGGER IF EXISTS audit_events_hash_chain ON accounting.audit_events;
CREATE TRIGGER audit_events_hash_chain BEFORE INSERT ON accounting.audit_events
 FOR EACH ROW EXECUTE FUNCTION accounting.audit_events_chain_guard();

CREATE TRIGGER audit_events_append_only_row
BEFORE UPDATE OR DELETE ON accounting.audit_events
FOR EACH ROW EXECUTE FUNCTION accounting.audit_events_append_only_guard();
