-- compatibility: incremental
-- B02 foundation: accounting policy, stable ledger account identity/version,
-- old-book account mapping, and an opening-balance preparation package.
-- Nothing here posts a journal, assigns a voucher number, or changes the
-- enterprise's authoritative book ownership.

CREATE TABLE IF NOT EXISTS accounting.accounting_policies(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 standard_kind TEXT NOT NULL CHECK(standard_kind IN('small_enterprise','enterprise')),
 taxpayer_kind TEXT NOT NULL CHECK(taxpayer_kind IN('general','small_scale','non_taxpayer')),
 industry TEXT NOT NULL DEFAULT '',
 fiscal_year_start_month INTEGER NOT NULL CHECK(fiscal_year_start_month BETWEEN 1 AND 12),
 currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','active','retired')),
 effective_from TEXT NOT NULL CHECK(effective_from ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 effective_to TEXT CHECK(effective_to IS NULL OR effective_to ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(ledger_id,version),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounting_policy_active
 ON accounting.accounting_policies(ledger_id) WHERE status='active';

CREATE TABLE IF NOT EXISTS accounting.ledger_accounts(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 current_version INTEGER NOT NULL CHECK(current_version>0),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','inactive')),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(id,ledger_id),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
CREATE TABLE IF NOT EXISTS accounting.ledger_account_versions(
 id TEXT PRIMARY KEY,
 account_id TEXT NOT NULL, ledger_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 code TEXT NOT NULL, name TEXT NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 120),
 parent_account_id TEXT,
 level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 6),
 is_leaf INTEGER NOT NULL CHECK(is_leaf IN(0,1)),
 category TEXT NOT NULL CHECK(category IN('asset','liability','equity','cost','income','expense','other')),
 normal_balance TEXT NOT NULL CHECK(normal_balance IN('debit','credit')),
 auxiliary_requirements TEXT NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(auxiliary_requirements::jsonb)='array'),
 effective_from TEXT NOT NULL CHECK(effective_from ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 effective_to TEXT CHECK(effective_to IS NULL OR effective_to ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('draft','active','retired')),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(account_id,version), UNIQUE(account_id,version,ledger_id),
 FOREIGN KEY(account_id,ledger_id) REFERENCES accounting.ledger_accounts(id,ledger_id),
 FOREIGN KEY(parent_account_id,ledger_id) REFERENCES accounting.ledger_accounts(id,ledger_id),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_account_versions_code
 ON accounting.ledger_account_versions(ledger_id,code,effective_from);

CREATE TABLE IF NOT EXISTS accounting.ledger_account_mappings(
 book_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 book_account_code TEXT NOT NULL, account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 PRIMARY KEY(book_id,book_account_code),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(account_id,account_version,ledger_id) REFERENCES accounting.ledger_account_versions(account_id,version,ledger_id)
);

CREATE TABLE IF NOT EXISTS accounting.opening_packages(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 target_period TEXT NOT NULL CHECK(target_period ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 policy_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','validated','submitted','approved','frozen','rejected')),
 current_version INTEGER NOT NULL DEFAULT 1 CHECK(current_version>0),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(ledger_id,target_period),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id),
 FOREIGN KEY(policy_id) REFERENCES accounting.accounting_policies(id)
);
CREATE TABLE IF NOT EXISTS accounting.opening_package_versions(
 id TEXT PRIMARY KEY,
 package_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 policy_version INTEGER NOT NULL CHECK(policy_version>0),
 config_fingerprint TEXT NOT NULL CHECK(length(config_fingerprint)=64),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','validated','submitted','approved','frozen','rejected','superseded')),
 request_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 supersedes_id TEXT,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 submitted_by TEXT REFERENCES accounting.users(id), submitted_at TEXT,
 approved_by TEXT REFERENCES accounting.users(id), approved_at TEXT, frozen_at TEXT,
 reason TEXT NOT NULL DEFAULT '',
 UNIQUE(package_id,version), UNIQUE(package_id,request_key),
 FOREIGN KEY(package_id) REFERENCES accounting.opening_packages(id),
 FOREIGN KEY(supersedes_id) REFERENCES accounting.opening_package_versions(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_opening_current_approved
 ON accounting.opening_package_versions(package_id) WHERE status IN('approved','frozen');

CREATE TABLE IF NOT EXISTS accounting.opening_lines(
 id TEXT PRIMARY KEY,
 package_version_id TEXT NOT NULL, line_no INTEGER NOT NULL CHECK(line_no>0),
 account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 aux_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(aux_json::jsonb)='object'),
 year_opening_cents BIGINT NOT NULL,
 ytd_debit_cents BIGINT NOT NULL CHECK(ytd_debit_cents>=0),
 ytd_credit_cents BIGINT NOT NULL CHECK(ytd_credit_cents>=0),
 opening_cents BIGINT NOT NULL,
 source_row_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(source_row_json::jsonb)='object'),
 UNIQUE(package_version_id,line_no),
 FOREIGN KEY(package_version_id) REFERENCES accounting.opening_package_versions(id),
 FOREIGN KEY(account_id,account_version) REFERENCES accounting.ledger_account_versions(account_id,version)
);
CREATE TABLE IF NOT EXISTS accounting.opening_open_items(
 id TEXT PRIMARY KEY,
 package_version_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('receivable','payable')),
 counterparty_id TEXT NOT NULL, original_date TEXT NOT NULL,
 remaining_cents BIGINT NOT NULL CHECK(remaining_cents>=0), source_ref TEXT NOT NULL DEFAULT '',
 FOREIGN KEY(package_version_id) REFERENCES accounting.opening_package_versions(id),
 FOREIGN KEY(counterparty_id) REFERENCES accounting.company_master_entities(id)
);
CREATE TABLE IF NOT EXISTS accounting.opening_checks(
 id TEXT PRIMARY KEY,
 package_version_id TEXT NOT NULL, check_code TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('pass','fail','blocked')),
 details_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(details_json::jsonb)='object'),
 checked_at TEXT NOT NULL,
 FOREIGN KEY(package_version_id) REFERENCES accounting.opening_package_versions(id)
);

ALTER TABLE accounting.accounting_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.accounting_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_account_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_account_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_account_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_account_mappings FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_packages FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_package_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_package_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_lines FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_open_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_open_items FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_checks FORCE ROW LEVEL SECURITY;

CREATE POLICY b02_policy_read ON accounting.accounting_policies FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=accounting_policies.ledger_id AND m.user_id=accounting.actor_user())));
CREATE POLICY b02_policy_write ON accounting.accounting_policies FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=accounting_policies.ledger_id AND m.user_id=accounting.actor_user())))
 WITH CHECK(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=accounting_policies.ledger_id AND m.user_id=accounting.actor_user())));

CREATE POLICY b02_accounts_read ON accounting.ledger_accounts FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_accounts.ledger_id AND m.user_id=accounting.actor_user())));
CREATE POLICY b02_accounts_write ON accounting.ledger_accounts FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_accounts.ledger_id AND m.user_id=accounting.actor_user())))
 WITH CHECK(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_accounts.ledger_id AND m.user_id=accounting.actor_user())));

CREATE POLICY b02_account_versions_read ON accounting.ledger_account_versions FOR SELECT TO accounting_app
 USING(ledger_id IN (SELECT id FROM accounting.ledger_accounts WHERE firm_id=accounting.actor_firm()
  AND (accounting.firm_manager(firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_accounts.ledger_id AND m.user_id=accounting.actor_user()))));
CREATE POLICY b02_account_versions_write ON accounting.ledger_account_versions FOR ALL TO accounting_app
 USING(ledger_id IN (SELECT id FROM accounting.ledger_accounts WHERE firm_id=accounting.actor_firm()
  AND (accounting.firm_manager(firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_accounts.ledger_id AND m.user_id=accounting.actor_user()))))
 WITH CHECK(ledger_id IN (SELECT id FROM accounting.ledger_accounts WHERE firm_id=accounting.actor_firm()
  AND (accounting.firm_manager(firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_accounts.ledger_id AND m.user_id=accounting.actor_user()))));

CREATE POLICY b02_mappings_read ON accounting.ledger_account_mappings FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_account_mappings.ledger_id AND m.user_id=accounting.actor_user())));
CREATE POLICY b02_mappings_write ON accounting.ledger_account_mappings FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_account_mappings.ledger_id AND m.user_id=accounting.actor_user())))
 WITH CHECK(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=ledger_account_mappings.ledger_id AND m.user_id=accounting.actor_user())));

CREATE POLICY b02_opening_read ON accounting.opening_packages FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=opening_packages.ledger_id AND m.user_id=accounting.actor_user())));
CREATE POLICY b02_opening_write ON accounting.opening_packages FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=opening_packages.ledger_id AND m.user_id=accounting.actor_user())))
 WITH CHECK(firm_id=accounting.actor_firm() AND (accounting.firm_manager(firm_id) OR EXISTS(
  SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=opening_packages.ledger_id AND m.user_id=accounting.actor_user())));

CREATE POLICY b02_opening_versions_read ON accounting.opening_package_versions FOR SELECT TO accounting_app
 USING(package_id IN (SELECT id FROM accounting.opening_packages WHERE firm_id=accounting.actor_firm()
  AND (accounting.firm_manager(firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=opening_packages.ledger_id AND m.user_id=accounting.actor_user()))));
CREATE POLICY b02_opening_versions_write ON accounting.opening_package_versions FOR ALL TO accounting_app
 USING(package_id IN (SELECT id FROM accounting.opening_packages WHERE firm_id=accounting.actor_firm()
  AND (accounting.firm_manager(firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=opening_packages.ledger_id AND m.user_id=accounting.actor_user()))))
 WITH CHECK(package_id IN (SELECT id FROM accounting.opening_packages WHERE firm_id=accounting.actor_firm()
  AND (accounting.firm_manager(firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=opening_packages.ledger_id AND m.user_id=accounting.actor_user()))));

CREATE POLICY b02_lines_read ON accounting.opening_lines FOR SELECT TO accounting_app
 USING(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))));
CREATE POLICY b02_lines_write ON accounting.opening_lines FOR ALL TO accounting_app
 USING(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))))
 WITH CHECK(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))));

CREATE POLICY b02_open_items_read ON accounting.opening_open_items FOR SELECT TO accounting_app
 USING(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))));
CREATE POLICY b02_open_items_write ON accounting.opening_open_items FOR ALL TO accounting_app
 USING(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))))
 WITH CHECK(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))));

CREATE POLICY b02_checks_read ON accounting.opening_checks FOR SELECT TO accounting_app
 USING(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))));
CREATE POLICY b02_checks_write ON accounting.opening_checks FOR ALL TO accounting_app
 USING(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))))
 WITH CHECK(package_version_id IN (SELECT v.id FROM accounting.opening_package_versions v JOIN accounting.opening_packages p ON p.id=v.package_id
  WHERE p.firm_id=accounting.actor_firm() AND (accounting.firm_manager(p.firm_id) OR EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=p.ledger_id AND m.user_id=accounting.actor_user()))));

REVOKE ALL ON accounting.accounting_policies,accounting.ledger_accounts,accounting.ledger_account_versions,
 accounting.ledger_account_mappings,accounting.opening_packages,accounting.opening_package_versions,
 accounting.opening_lines,accounting.opening_open_items,accounting.opening_checks FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT,UPDATE ON accounting.accounting_policies,accounting.ledger_accounts,
 accounting.ledger_account_versions,accounting.ledger_account_mappings,accounting.opening_packages,
 accounting.opening_package_versions,accounting.opening_lines,accounting.opening_open_items,
 accounting.opening_checks TO accounting_app;

CREATE TRIGGER b02_policy_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.accounting_policies
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_accounts_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ledger_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_account_versions_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ledger_account_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_mappings_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ledger_account_mappings
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_opening_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_packages
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_opening_versions_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_package_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_lines_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_open_items_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_open_items
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER b02_checks_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_checks
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();

CREATE OR REPLACE FUNCTION accounting.opening_frozen_guard()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='ledger_account_versions' AND TG_OP IN('UPDATE','DELETE') THEN
  RAISE EXCEPTION 'Ledger account versions are append-only' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='opening_lines' THEN
  IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM accounting.opening_package_versions v
    WHERE v.id=OLD.package_version_id AND v.status IN('approved','frozen')) THEN
    RAISE EXCEPTION 'Frozen opening lines cannot be deleted' USING ERRCODE='23514';
   END IF;
  ELSE
   IF EXISTS(SELECT 1 FROM accounting.opening_package_versions v
    WHERE v.id=NEW.package_version_id AND v.status IN('approved','frozen')) THEN
    RAISE EXCEPTION 'Frozen opening lines cannot be changed' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN COALESCE(NEW,OLD);
END $$;
REVOKE ALL ON FUNCTION accounting.opening_frozen_guard() FROM PUBLIC;
CREATE TRIGGER b02_ledger_account_versions_append_only BEFORE UPDATE OR DELETE ON accounting.ledger_account_versions
 FOR EACH ROW EXECUTE FUNCTION accounting.opening_frozen_guard();
CREATE TRIGGER b02_opening_lines_frozen BEFORE INSERT OR UPDATE OR DELETE ON accounting.opening_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.opening_frozen_guard();
