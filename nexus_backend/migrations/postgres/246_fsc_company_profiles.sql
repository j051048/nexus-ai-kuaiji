CREATE TABLE IF NOT EXISTS accounting.fsc_company_profiles (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  status TEXT NOT NULL CHECK(status IN ('draft','confirmed','superseded')),
  industry TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  confirmations_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TEXT NOT NULL,
  confirmed_by TEXT REFERENCES accounting.users(id),
  confirmed_at TEXT,
  UNIQUE(company_id,version),
  FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
CREATE INDEX IF NOT EXISTS idx_fsc_profiles_company
  ON accounting.fsc_company_profiles(firm_id,company_id,status,version DESC);
CREATE OR REPLACE FUNCTION accounting.fsc_profile_confirmed_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status='confirmed' AND (
    NEW.company_id<>OLD.company_id OR NEW.firm_id<>OLD.firm_id OR NEW.version<>OLD.version
    OR NEW.industry<>OLD.industry OR NEW.payload_json<>OLD.payload_json
    OR NEW.confirmations_json<>OLD.confirmations_json OR NEW.fingerprint<>OLD.fingerprint
    OR NEW.confirmed_by IS DISTINCT FROM OLD.confirmed_by
    OR NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at) THEN
    RAISE EXCEPTION 'confirmed FSC profile is immutable';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS fsc_profile_confirmed_immutable ON accounting.fsc_company_profiles;
CREATE TRIGGER fsc_profile_confirmed_immutable
  BEFORE UPDATE ON accounting.fsc_company_profiles
  FOR EACH ROW EXECUTE FUNCTION accounting.fsc_profile_confirmed_immutable();
ALTER TABLE accounting.fsc_company_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY fsc_company_profiles_company ON accounting.fsc_company_profiles
  USING (accounting.company_visible(company_id) AND firm_id=accounting.actor_firm())
  WITH CHECK (accounting.company_visible(company_id) AND firm_id=accounting.actor_firm());
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_company_profiles TO accounting_app;
