-- Creation is idempotent per applicant and current firm, including retries
-- after a lost HTTP response. Originals keep their first request fingerprint.
ALTER TABLE accounting.business_events ADD COLUMN create_key TEXT;
ALTER TABLE accounting.business_events ADD COLUMN creation_fingerprint TEXT;
ALTER TABLE accounting.business_events ADD CONSTRAINT business_creation_key_pair
 CHECK((create_key IS NULL AND creation_fingerprint IS NULL) OR (create_key IS NOT NULL AND creation_fingerprint IS NOT NULL));
CREATE UNIQUE INDEX idx_business_event_create_key ON accounting.business_events(firm_id,applicant_id,create_key) WHERE create_key IS NOT NULL;
CREATE FUNCTION accounting.business_create_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.create_key,NEW.creation_fingerprint) IS DISTINCT FROM (OLD.create_key,OLD.creation_fingerprint) THEN
   RAISE EXCEPTION 'business creation key is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
REVOKE ALL ON FUNCTION accounting.business_create_identity_guard() FROM PUBLIC;
CREATE TRIGGER business_events_create_identity BEFORE UPDATE ON accounting.business_events
 FOR EACH ROW EXECUTE FUNCTION accounting.business_create_identity_guard();
