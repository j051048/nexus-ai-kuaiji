-- compatibility: incremental
CREATE TABLE IF NOT EXISTS accounting.tax_pilot_profiles (
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
CREATE INDEX IF NOT EXISTS idx_tax_pilot_profiles_company ON accounting.tax_pilot_profiles(company_id,status,version DESC);
CREATE TABLE IF NOT EXISTS accounting.tax_pilot_dependencies (
 id TEXT PRIMARY KEY,
 profile_id TEXT NOT NULL REFERENCES accounting.tax_pilot_profiles(id) ON DELETE RESTRICT,
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
CREATE TABLE IF NOT EXISTS accounting.tax_pilot_previews (
 id TEXT PRIMARY KEY,
 profile_id TEXT NOT NULL REFERENCES accounting.tax_pilot_profiles(id) ON DELETE RESTRICT,
 purpose TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 invalidated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tax_pilot_previews_profile ON accounting.tax_pilot_previews(profile_id,created_at);
ALTER TABLE accounting.tax_pilot_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_pilot_profiles FORCE ROW LEVEL SECURITY;
CREATE POLICY tax_pilot_profiles_read ON accounting.tax_pilot_profiles FOR SELECT TO accounting_app USING(accounting.company_visible(company_id));
CREATE POLICY tax_pilot_profiles_write ON accounting.tax_pilot_profiles FOR ALL TO accounting_app USING(accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id)) WITH CHECK(accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));
ALTER TABLE accounting.tax_pilot_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_pilot_dependencies FORCE ROW LEVEL SECURITY;
CREATE POLICY tax_pilot_dependencies_read ON accounting.tax_pilot_dependencies FOR SELECT TO accounting_app USING(EXISTS(SELECT 1 FROM accounting.tax_pilot_profiles p WHERE p.id=profile_id AND accounting.company_visible(p.company_id)));
CREATE POLICY tax_pilot_dependencies_write ON accounting.tax_pilot_dependencies FOR ALL TO accounting_app USING(EXISTS(SELECT 1 FROM accounting.tax_pilot_profiles p WHERE p.id=profile_id AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(p.company_id))) WITH CHECK(EXISTS(SELECT 1 FROM accounting.tax_pilot_profiles p WHERE p.id=profile_id AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(p.company_id)));
ALTER TABLE accounting.tax_pilot_previews ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_pilot_previews FORCE ROW LEVEL SECURITY;
CREATE POLICY tax_pilot_previews_read ON accounting.tax_pilot_previews FOR SELECT TO accounting_app USING(EXISTS(SELECT 1 FROM accounting.tax_pilot_profiles p WHERE p.id=profile_id AND accounting.company_visible(p.company_id)));
CREATE POLICY tax_pilot_previews_write ON accounting.tax_pilot_previews FOR ALL TO accounting_app USING(EXISTS(SELECT 1 FROM accounting.tax_pilot_profiles p WHERE p.id=profile_id AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(p.company_id))) WITH CHECK(EXISTS(SELECT 1 FROM accounting.tax_pilot_profiles p WHERE p.id=profile_id AND accounting.company_visible(p.company_id)));
GRANT SELECT,INSERT,UPDATE ON accounting.tax_pilot_profiles,accounting.tax_pilot_dependencies,accounting.tax_pilot_previews TO accounting_app;
REVOKE ALL ON accounting.tax_pilot_profiles,accounting.tax_pilot_dependencies,accounting.tax_pilot_previews FROM PUBLIC;
