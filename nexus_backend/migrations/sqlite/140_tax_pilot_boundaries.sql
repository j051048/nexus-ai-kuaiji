-- compatibility: incremental
CREATE TABLE IF NOT EXISTS tax_pilot_profiles (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>=1),
 status TEXT NOT NULL CHECK(status IN('draft','confirmed','superseded')),
 authority_mode TEXT NOT NULL DEFAULT '',
 config_json TEXT NOT NULL DEFAULT '{}',
 fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 confirmed_by TEXT,
 confirmed_at TEXT,
 UNIQUE(company_id,version)
);
CREATE INDEX IF NOT EXISTS idx_tax_pilot_profiles_company ON tax_pilot_profiles(company_id,status,version DESC);
CREATE TABLE IF NOT EXISTS tax_pilot_dependencies (
 id TEXT PRIMARY KEY,
 profile_id TEXT NOT NULL REFERENCES tax_pilot_profiles(id) ON DELETE RESTRICT,
 code TEXT NOT NULL,
 label TEXT NOT NULL,
 required INTEGER NOT NULL CHECK(required IN(0,1)),
 status TEXT NOT NULL CHECK(status IN('unresolved','confirmed','not_applicable')),
 owner_role TEXT NOT NULL,
 evidence_ref TEXT NOT NULL DEFAULT '',
 impact TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 updated_by TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(profile_id,code)
);
CREATE TABLE IF NOT EXISTS tax_pilot_previews (
 id TEXT PRIMARY KEY,
 profile_id TEXT NOT NULL REFERENCES tax_pilot_profiles(id) ON DELETE RESTRICT,
 purpose TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 invalidated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tax_pilot_previews_profile ON tax_pilot_previews(profile_id,created_at);
