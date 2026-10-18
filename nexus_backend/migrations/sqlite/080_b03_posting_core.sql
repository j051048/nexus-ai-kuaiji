-- compatibility: incremental
CREATE TABLE opening_bindings(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL, book_id TEXT NOT NULL,
 period TEXT NOT NULL, package_version_id TEXT NOT NULL, snapshot_fingerprint TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','superseded')),
 bound_by TEXT NOT NULL REFERENCES users(id), bound_at TEXT NOT NULL,
 UNIQUE(ledger_id,period),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(package_version_id) REFERENCES opening_package_versions(id)
);

CREATE TABLE vouchers(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL, book_id TEXT NOT NULL,
 period TEXT NOT NULL, voucher_type TEXT NOT NULL DEFAULT '记' CHECK(voucher_type IN('记','收','付','转')),
 voucher_no INTEGER NOT NULL CHECK(voucher_no>0), voucher_date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'posted' CHECK(status='posted'),
 description TEXT NOT NULL, source_kind TEXT NOT NULL CHECK(source_kind IN('draft','manual','reversal','adjustment')),
 source_id TEXT, source_version INTEGER,
 opening_binding_id TEXT NOT NULL REFERENCES opening_bindings(id),
 review_policy_version TEXT NOT NULL, content_fingerprint TEXT NOT NULL,
 reversal_of TEXT REFERENCES vouchers(id), relation_kind TEXT CHECK(relation_kind IN('reversal','adjustment')),
 command_id TEXT NOT NULL, posted_by TEXT NOT NULL REFERENCES users(id), posted_at TEXT NOT NULL,
 UNIQUE(book_id,voucher_type,voucher_no),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE INDEX idx_vouchers_book_date ON vouchers(book_id,voucher_date,voucher_type,voucher_no);
CREATE TABLE voucher_lines(
 id TEXT PRIMARY KEY, voucher_id TEXT NOT NULL REFERENCES vouchers(id), line_no INTEGER NOT NULL CHECK(line_no>0),
 account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 code_snapshot TEXT NOT NULL, name_snapshot TEXT NOT NULL, category_snapshot TEXT NOT NULL,
 normal_balance_snapshot TEXT NOT NULL,
 debit_cents INTEGER NOT NULL CHECK(debit_cents>=0), credit_cents INTEGER NOT NULL CHECK(credit_cents>=0),
 aux_json TEXT NOT NULL DEFAULT '{}', source_ref TEXT NOT NULL DEFAULT '',
 UNIQUE(voucher_id,line_no),
 CHECK((debit_cents>0 AND credit_cents=0) OR (credit_cents>0 AND debit_cents=0)),
 FOREIGN KEY(account_id,account_version) REFERENCES ledger_account_versions(account_id,version)
);
CREATE TABLE voucher_sequences(
 book_id TEXT NOT NULL, voucher_type TEXT NOT NULL CHECK(voucher_type IN('记','收','付','转')),
 next_no INTEGER NOT NULL CHECK(next_no>0), PRIMARY KEY(book_id,voucher_type),
 FOREIGN KEY(book_id) REFERENCES books(id)
);
CREATE TABLE posting_commands(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 command_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL, voucher_id TEXT NOT NULL REFERENCES vouchers(id),
 result_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(firm_id,book_id,command_key),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE voucher_sources(
 id TEXT PRIMARY KEY, voucher_id TEXT NOT NULL REFERENCES vouchers(id),
 source_kind TEXT NOT NULL CHECK(source_kind IN('draft','source','manual')), source_id TEXT NOT NULL,
 source_version INTEGER, amount_cents INTEGER NOT NULL CHECK(amount_cents>=0), evidence_ref TEXT NOT NULL DEFAULT ''
);
CREATE TABLE voucher_relations(
 id TEXT PRIMARY KEY, original_voucher_id TEXT NOT NULL REFERENCES vouchers(id),
 related_voucher_id TEXT NOT NULL UNIQUE REFERENCES vouchers(id),
 kind TEXT NOT NULL CHECK(kind IN('reversal','adjustment')), reason TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_voucher_full_reversal ON voucher_relations(original_voucher_id) WHERE kind='reversal';

CREATE TRIGGER vouchers_immutable_update BEFORE UPDATE ON vouchers
BEGIN SELECT RAISE(ABORT,'formal vouchers are append-only'); END;
CREATE TRIGGER vouchers_immutable_delete BEFORE DELETE ON vouchers
BEGIN SELECT RAISE(ABORT,'formal vouchers are append-only'); END;
CREATE TRIGGER voucher_lines_immutable_update BEFORE UPDATE ON voucher_lines
BEGIN SELECT RAISE(ABORT,'formal voucher lines are append-only'); END;
CREATE TRIGGER voucher_lines_immutable_delete BEFORE DELETE ON voucher_lines
BEGIN SELECT RAISE(ABORT,'formal voucher lines are append-only'); END;
CREATE TRIGGER voucher_relations_immutable_update BEFORE UPDATE ON voucher_relations
BEGIN SELECT RAISE(ABORT,'voucher relations are append-only'); END;
CREATE TRIGGER voucher_relations_immutable_delete BEFORE DELETE ON voucher_relations
BEGIN SELECT RAISE(ABORT,'voucher relations are append-only'); END;
