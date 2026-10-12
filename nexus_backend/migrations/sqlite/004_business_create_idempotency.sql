-- A retried create request reuses the applicant's form key. Null preserves
-- compatibility with internal callers that do not request idempotent creation.
ALTER TABLE business_events ADD COLUMN create_key TEXT;
ALTER TABLE business_events ADD COLUMN creation_fingerprint TEXT
 CHECK((create_key IS NULL AND creation_fingerprint IS NULL) OR (create_key IS NOT NULL AND creation_fingerprint IS NOT NULL));
CREATE UNIQUE INDEX idx_business_event_create_key ON business_events(firm_id,applicant_id,create_key) WHERE create_key IS NOT NULL;
CREATE TRIGGER business_events_create_identity BEFORE UPDATE OF create_key,creation_fingerprint ON business_events
 WHEN NEW.create_key IS NOT OLD.create_key OR NEW.creation_fingerprint IS NOT OLD.creation_fingerprint
 BEGIN SELECT RAISE(ABORT,'business creation key is immutable'); END;
