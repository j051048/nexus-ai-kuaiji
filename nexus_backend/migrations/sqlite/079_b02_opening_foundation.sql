-- compatibility: incremental
CREATE TABLE accounting_policies(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 standard_kind TEXT NOT NULL CHECK(standard_kind IN('small_enterprise','enterprise')),
 taxpayer_kind TEXT NOT NULL CHECK(taxpayer_kind IN('general','small_scale','non_taxpayer')),
 industry TEXT NOT NULL DEFAULT '',
 fiscal_year_start_month INTEGER NOT NULL CHECK(fiscal_year_start_month BETWEEN 1 AND 12),
 currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','active','retired')),
 effective_from TEXT NOT NULL, effective_to TEXT,
 reason TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(ledger_id,version),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id)
);
CREATE UNIQUE INDEX idx_accounting_policy_active ON accounting_policies(ledger_id) WHERE status='active';

CREATE TABLE ledger_accounts(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 current_version INTEGER NOT NULL CHECK(current_version>0),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','inactive')),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(id,ledger_id),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id)
);
CREATE TABLE ledger_account_versions(
 id TEXT PRIMARY KEY,
 account_id TEXT NOT NULL, ledger_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 code TEXT NOT NULL, name TEXT NOT NULL,
 parent_account_id TEXT, level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 6),
 is_leaf INTEGER NOT NULL CHECK(is_leaf IN(0,1)),
 category TEXT NOT NULL, normal_balance TEXT NOT NULL CHECK(normal_balance IN('debit','credit')),
 auxiliary_requirements TEXT NOT NULL DEFAULT '[]',
 effective_from TEXT NOT NULL, effective_to TEXT,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('draft','active','retired')),
 reason TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(account_id,version), UNIQUE(account_id,version,ledger_id),
 FOREIGN KEY(account_id,ledger_id) REFERENCES ledger_accounts(id,ledger_id),
 FOREIGN KEY(parent_account_id,ledger_id) REFERENCES ledger_accounts(id,ledger_id),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id)
);
CREATE INDEX idx_ledger_account_versions_code ON ledger_account_versions(ledger_id,code,effective_from);

CREATE TABLE ledger_account_mappings(
 book_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 book_account_code TEXT NOT NULL, account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 PRIMARY KEY(book_id,book_account_code),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(account_id,account_version,ledger_id) REFERENCES ledger_account_versions(account_id,version,ledger_id)
);

CREATE TABLE opening_packages(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 target_period TEXT NOT NULL, policy_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','validated','submitted','approved','frozen','rejected')),
 current_version INTEGER NOT NULL DEFAULT 1 CHECK(current_version>0),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(ledger_id,target_period),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id),
 FOREIGN KEY(policy_id) REFERENCES accounting_policies(id)
);
CREATE TABLE opening_package_versions(
 id TEXT PRIMARY KEY,
 package_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 policy_version INTEGER NOT NULL CHECK(policy_version>0),
 config_fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft'
  CHECK(status IN('draft','validated','submitted','approved','frozen','rejected','superseded')),
 request_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL, supersedes_id TEXT,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 submitted_by TEXT REFERENCES users(id), submitted_at TEXT,
 approved_by TEXT REFERENCES users(id), approved_at TEXT, frozen_at TEXT,
 reason TEXT NOT NULL DEFAULT '',
 UNIQUE(package_id,version), UNIQUE(package_id,request_key),
 FOREIGN KEY(package_id) REFERENCES opening_packages(id),
 FOREIGN KEY(supersedes_id) REFERENCES opening_package_versions(id)
);
CREATE UNIQUE INDEX idx_opening_current_approved
 ON opening_package_versions(package_id) WHERE status IN('approved','frozen');

CREATE TABLE opening_lines(
 id TEXT PRIMARY KEY,
 package_version_id TEXT NOT NULL, line_no INTEGER NOT NULL CHECK(line_no>0),
 account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 aux_json TEXT NOT NULL DEFAULT '{}',
 year_opening_cents INTEGER NOT NULL,
 ytd_debit_cents INTEGER NOT NULL CHECK(ytd_debit_cents>=0),
 ytd_credit_cents INTEGER NOT NULL CHECK(ytd_credit_cents>=0),
 opening_cents INTEGER NOT NULL,
 source_row_json TEXT NOT NULL DEFAULT '{}',
 UNIQUE(package_version_id,line_no),
 FOREIGN KEY(package_version_id) REFERENCES opening_package_versions(id),
 FOREIGN KEY(account_id,account_version) REFERENCES ledger_account_versions(account_id,version)
);
CREATE TABLE opening_open_items(
 id TEXT PRIMARY KEY,
 package_version_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('receivable','payable')),
 counterparty_id TEXT NOT NULL, original_date TEXT NOT NULL,
 remaining_cents INTEGER NOT NULL CHECK(remaining_cents>=0), source_ref TEXT NOT NULL DEFAULT '',
 FOREIGN KEY(package_version_id) REFERENCES opening_package_versions(id),
 FOREIGN KEY(counterparty_id) REFERENCES company_master_entities(id)
);
CREATE TABLE opening_checks(
 id TEXT PRIMARY KEY,
 package_version_id TEXT NOT NULL, check_code TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('pass','fail','blocked')),
 details_json TEXT NOT NULL DEFAULT '{}', checked_at TEXT NOT NULL,
 FOREIGN KEY(package_version_id) REFERENCES opening_package_versions(id)
);

CREATE TRIGGER ledger_account_versions_append_only_update
BEFORE UPDATE ON ledger_account_versions
BEGIN SELECT RAISE(ABORT,'ledger account versions are append-only'); END;
CREATE TRIGGER ledger_account_versions_append_only_delete
BEFORE DELETE ON ledger_account_versions
BEGIN SELECT RAISE(ABORT,'ledger account versions are append-only'); END;
CREATE TRIGGER opening_lines_frozen_insert
BEFORE INSERT ON opening_lines
WHEN EXISTS(SELECT 1 FROM opening_package_versions v
 WHERE v.id=NEW.package_version_id AND v.status IN('approved','frozen'))
BEGIN SELECT RAISE(ABORT,'frozen opening lines cannot be changed'); END;
CREATE TRIGGER opening_lines_frozen_update
BEFORE UPDATE ON opening_lines
WHEN EXISTS(SELECT 1 FROM opening_package_versions v
 WHERE v.id=OLD.package_version_id AND v.status IN('approved','frozen'))
BEGIN SELECT RAISE(ABORT,'frozen opening lines cannot be changed'); END;
CREATE TRIGGER opening_lines_frozen_delete
BEFORE DELETE ON opening_lines
WHEN EXISTS(SELECT 1 FROM opening_package_versions v
 WHERE v.id=OLD.package_version_id AND v.status IN('approved','frozen'))
BEGIN SELECT RAISE(ABORT,'frozen opening lines cannot be deleted'); END;
