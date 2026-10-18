-- compatibility: incremental
-- B04: long-term open items, deterministic periodic drafts, financial report
-- snapshots and internal month-close locks. No real tenant switch.

CREATE TABLE subledger_items(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL, book_id TEXT NOT NULL,
 period TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('receivable','payable','advance_received','advance_paid')),
 counterparty_id TEXT NOT NULL,
 control_account_id TEXT NOT NULL, control_account_version INTEGER NOT NULL,
 original_date TEXT NOT NULL, due_date TEXT,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 source_kind TEXT NOT NULL CHECK(source_kind IN('opening','voucher','manual')),
 source_id TEXT, source_version INTEGER,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','partial','settled','cancelled')),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(control_account_id,control_account_version) REFERENCES ledger_account_versions(account_id,version),
 FOREIGN KEY(counterparty_id) REFERENCES company_master_entities(id)
);
CREATE INDEX idx_subledger_items_scope ON subledger_items(ledger_id,kind,status,due_date);
CREATE TABLE subledger_allocations(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 item_id TEXT NOT NULL REFERENCES subledger_items(id),
 voucher_id TEXT NOT NULL REFERENCES vouchers(id),
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), allocated_on TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(item_id,voucher_id), UNIQUE(firm_id,idempotency_key)
);
CREATE TABLE subledger_allocation_reversals(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 allocation_id TEXT NOT NULL UNIQUE REFERENCES subledger_allocations(id),
 reason TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(firm_id,idempotency_key)
);
CREATE TABLE bank_statement_lines(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL,
 line_date TEXT NOT NULL, description TEXT NOT NULL, amount_cents INTEGER NOT NULL,
 matched_voucher_line_id TEXT REFERENCES voucher_lines(id), status TEXT NOT NULL DEFAULT 'unmatched'
  CHECK(status IN('unmatched','matched','excluded')),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE bank_recon_adjustments(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('outstanding_deposit','outstanding_check','bank_fee','interest','error','other')),
 amount_cents INTEGER NOT NULL, explanation TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','explained','approved')),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);

CREATE TABLE periodic_rules(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 book_id TEXT NOT NULL REFERENCES books(id),
 kind TEXT NOT NULL CHECK(kind IN('accrual','depreciation','amortization','closing')),
 name TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1,
 version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE periodic_assets(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), kind TEXT NOT NULL CHECK(kind IN('fixed','intangible')),
 name TEXT NOT NULL, cost_cents INTEGER NOT NULL CHECK(cost_cents>0), residual_cents INTEGER NOT NULL DEFAULT 0 CHECK(residual_cents>=0),
 life_months INTEGER NOT NULL CHECK(life_months>0), start_period TEXT NOT NULL,
 accumulated_cents INTEGER NOT NULL DEFAULT 0 CHECK(accumulated_cents>=0),
 expense_account_id TEXT NOT NULL, expense_account_version INTEGER NOT NULL,
 accum_account_id TEXT NOT NULL, accum_account_version INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','disposed')), disposed_period TEXT,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 FOREIGN KEY(expense_account_id,expense_account_version) REFERENCES ledger_account_versions(account_id,version),
 FOREIGN KEY(accum_account_id,accum_account_version) REFERENCES ledger_account_versions(account_id,version)
);
CREATE TABLE periodic_runs(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL,
 kind TEXT NOT NULL, run_key TEXT NOT NULL, asset_id TEXT, input_fingerprint TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'generated' CHECK(status IN('generated','approved','posted','void')),
 draft_id TEXT, voucher_id TEXT REFERENCES vouchers(id), created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL, UNIQUE(book_id,run_key)
);
CREATE TABLE periodic_drafts(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES periodic_runs(id), book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','approved','posted')),
 version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 approved_by TEXT REFERENCES users(id), approved_at TEXT, posted_voucher_id TEXT REFERENCES vouchers(id)
);
CREATE TABLE periodic_draft_lines(
 id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES periodic_drafts(id), line_no INTEGER NOT NULL,
 account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 debit_cents INTEGER NOT NULL CHECK(debit_cents>=0), credit_cents INTEGER NOT NULL CHECK(credit_cents>=0),
 aux_json TEXT NOT NULL DEFAULT '{}', source_ref TEXT NOT NULL DEFAULT '',
 UNIQUE(draft_id,line_no),
 CHECK((debit_cents>0 AND credit_cents=0) OR (credit_cents>0 AND debit_cents=0)),
 FOREIGN KEY(account_id,account_version) REFERENCES ledger_account_versions(account_id,version)
);

CREATE TABLE report_templates(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, standard TEXT NOT NULL, version INTEGER NOT NULL,
 effective_from TEXT NOT NULL, definition_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, UNIQUE(id,version)
);
CREATE TABLE report_runs(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL,
 template_id TEXT NOT NULL, template_version INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','validated','reviewed','published','expired','superseded')),
 version INTEGER NOT NULL, inputs_fingerprint TEXT NOT NULL,
 content_json TEXT NOT NULL, content_hash TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT,
 published_by TEXT REFERENCES users(id), published_at TEXT,
 supersedes_id TEXT REFERENCES report_runs(id),
 UNIQUE(book_id,period,template_id,version)
);
CREATE TABLE report_notes(
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES report_runs(id), note_key TEXT NOT NULL,
 text TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(report_id,note_key)
);
CREATE TABLE internal_close_runs(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','preflight_failed','preflight_passed','reviewed','locked','published')),
 version INTEGER NOT NULL, inputs_fingerprint TEXT NOT NULL, checks_json TEXT NOT NULL,
 report_run_id TEXT REFERENCES report_runs(id), created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL, reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT,
 locked_by TEXT REFERENCES users(id), locked_at TEXT, published_by TEXT REFERENCES users(id), published_at TEXT,
 UNIQUE(book_id,period,version)
);
CREATE TABLE internal_period_locks(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL, close_run_id TEXT NOT NULL REFERENCES internal_close_runs(id),
 locked_by TEXT NOT NULL REFERENCES users(id), locked_at TEXT NOT NULL, UNIQUE(book_id,period)
);

CREATE TRIGGER subledger_items_no_update BEFORE UPDATE ON subledger_items
BEGIN SELECT RAISE(ABORT,'subledger items are append-only'); END;
CREATE TRIGGER subledger_items_no_delete BEFORE DELETE ON subledger_items
BEGIN SELECT RAISE(ABORT,'subledger items are append-only'); END;
CREATE TRIGGER subledger_allocations_no_update BEFORE UPDATE ON subledger_allocations
BEGIN SELECT RAISE(ABORT,'subledger allocations are append-only'); END;
CREATE TRIGGER subledger_allocations_no_delete BEFORE DELETE ON subledger_allocations
BEGIN SELECT RAISE(ABORT,'subledger allocations are append-only'); END;
CREATE TRIGGER subledger_reversals_no_update BEFORE UPDATE ON subledger_allocation_reversals
BEGIN SELECT RAISE(ABORT,'subledger allocation reversals are append-only'); END;
CREATE TRIGGER subledger_reversals_no_delete BEFORE DELETE ON subledger_allocation_reversals
BEGIN SELECT RAISE(ABORT,'subledger allocation reversals are append-only'); END;
CREATE TRIGGER periodic_drafts_no_delete BEFORE DELETE ON periodic_drafts
BEGIN SELECT RAISE(ABORT,'periodic drafts are append-only'); END;
CREATE TRIGGER report_runs_published_immutable BEFORE UPDATE ON report_runs
WHEN OLD.status='published' AND NEW.status IN('draft','validated','reviewed','expired','superseded')
BEGIN SELECT RAISE(ABORT,'published report is immutable'); END;
CREATE TRIGGER report_runs_published_delete BEFORE DELETE ON report_runs
WHEN OLD.status='published'
BEGIN SELECT RAISE(ABORT,'published report is immutable'); END;
CREATE TRIGGER report_runs_published_content BEFORE UPDATE OF content_json,content_hash,inputs_fingerprint,
 reviewed_by,reviewed_at ON report_runs
WHEN OLD.status='published'
BEGIN SELECT RAISE(ABORT,'published report content is immutable'); END;
CREATE TRIGGER internal_locks_no_update BEFORE UPDATE ON internal_period_locks
BEGIN SELECT RAISE(ABORT,'period locks are append-only'); END;
CREATE TRIGGER internal_locks_no_delete BEFORE DELETE ON internal_period_locks
BEGIN SELECT RAISE(ABORT,'period locks are append-only'); END;
