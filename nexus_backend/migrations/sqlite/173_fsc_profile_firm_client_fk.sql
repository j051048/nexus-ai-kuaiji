-- Fix the 162 composite foreign key column order without editing applied SQL.
DROP TRIGGER IF EXISTS fsc_profile_confirmed_immutable;
DROP TRIGGER IF EXISTS fsc_profile_delete_guard;
DROP INDEX IF EXISTS idx_fsc_profiles_company;
ALTER TABLE fsc_company_profiles RENAME TO fsc_company_profiles_legacy;
CREATE TABLE fsc_company_profiles (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL CHECK(status IN ('draft','confirmed','superseded')),
 industry TEXT NOT NULL CHECK(length(trim(industry)) BETWEEN 1 AND 80),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
 confirmations_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(confirmations_json) AND json_type(confirmations_json)='object'),
 fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 confirmed_by TEXT REFERENCES users(id),
 confirmed_at TEXT,
 UNIQUE(company_id,version),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
INSERT INTO fsc_company_profiles(
 id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,
 fingerprint,created_by,created_at,confirmed_by,confirmed_at)
SELECT id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,
 fingerprint,created_by,created_at,confirmed_by,confirmed_at
FROM fsc_company_profiles_legacy;
DROP TABLE fsc_company_profiles_legacy;
CREATE INDEX idx_fsc_profiles_company
 ON fsc_company_profiles(firm_id,company_id,status,version DESC);
CREATE TRIGGER fsc_profile_confirmed_immutable
BEFORE UPDATE ON fsc_company_profiles
WHEN OLD.status='confirmed' AND (
 NEW.company_id<>OLD.company_id OR NEW.firm_id<>OLD.firm_id OR NEW.version<>OLD.version
 OR NEW.industry<>OLD.industry OR NEW.payload_json<>OLD.payload_json
 OR NEW.confirmations_json<>OLD.confirmations_json OR NEW.fingerprint<>OLD.fingerprint
 OR NEW.confirmed_by IS NOT OLD.confirmed_by OR NEW.confirmed_at IS NOT OLD.confirmed_at)
BEGIN SELECT RAISE(ABORT,'confirmed FSC profile is immutable'); END;
CREATE TRIGGER fsc_profile_delete_guard
BEFORE DELETE ON fsc_company_profiles
BEGIN SELECT RAISE(ABORT,'FSC profiles are not deleted'); END;
