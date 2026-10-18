-- compatibility: incremental
-- B04 subledger, periodic drafts, report snapshots and internal close locks.

CREATE TABLE IF NOT EXISTS accounting.subledger_items(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 book_id TEXT NOT NULL, period TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('receivable','payable','advance_received','advance_paid')),
 counterparty_id TEXT NOT NULL, control_account_id TEXT NOT NULL, control_account_version INTEGER NOT NULL,
 original_date TEXT NOT NULL, due_date TEXT, amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
 source_kind TEXT NOT NULL CHECK(source_kind IN('opening','voucher','manual')), source_id TEXT, source_version INTEGER,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','partial','settled','cancelled')),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(control_account_id,control_account_version) REFERENCES accounting.ledger_account_versions(account_id,version),
 FOREIGN KEY(counterparty_id) REFERENCES accounting.company_master_entities(id)
);
CREATE TABLE IF NOT EXISTS accounting.subledger_allocations(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 item_id TEXT NOT NULL REFERENCES accounting.subledger_items(id), voucher_id TEXT NOT NULL REFERENCES accounting.vouchers(id),
 amount_cents BIGINT NOT NULL CHECK(amount_cents>0), allocated_on TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(item_id,voucher_id), UNIQUE(firm_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS accounting.subledger_allocation_reversals(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 allocation_id TEXT NOT NULL UNIQUE REFERENCES accounting.subledger_allocations(id),
 reason TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(firm_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS accounting.bank_statement_lines(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL,
 line_date TEXT NOT NULL, description TEXT NOT NULL, amount_cents BIGINT NOT NULL,
 matched_voucher_line_id TEXT REFERENCES accounting.voucher_lines(id),
 status TEXT NOT NULL DEFAULT 'unmatched' CHECK(status IN('unmatched','matched','excluded')),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounting.bank_recon_adjustments(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('outstanding_deposit','outstanding_check','bank_fee','interest','error','other')),
 amount_cents BIGINT NOT NULL, explanation TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','explained','approved')),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounting.periodic_rules(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 kind TEXT NOT NULL CHECK(kind IN('accrual','depreciation','amortization','closing')),
 name TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1,
 version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE IF NOT EXISTS accounting.periodic_assets(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES accounting.books(id),
 kind TEXT NOT NULL CHECK(kind IN('fixed','intangible')), name TEXT NOT NULL,
 cost_cents BIGINT NOT NULL CHECK(cost_cents>0), residual_cents BIGINT NOT NULL DEFAULT 0 CHECK(residual_cents>=0),
 life_months INTEGER NOT NULL CHECK(life_months>0), start_period TEXT NOT NULL,
 accumulated_cents BIGINT NOT NULL DEFAULT 0 CHECK(accumulated_cents>=0),
 expense_account_id TEXT NOT NULL, expense_account_version INTEGER NOT NULL,
 accum_account_id TEXT NOT NULL, accum_account_version INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','disposed')), disposed_period TEXT,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 FOREIGN KEY(expense_account_id,expense_account_version) REFERENCES accounting.ledger_account_versions(account_id,version),
 FOREIGN KEY(accum_account_id,accum_account_version) REFERENCES accounting.ledger_account_versions(account_id,version)
);
CREATE TABLE IF NOT EXISTS accounting.periodic_runs(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL,
 kind TEXT NOT NULL, run_key TEXT NOT NULL, asset_id TEXT, input_fingerprint TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'generated' CHECK(status IN('generated','approved','posted','void')),
 draft_id TEXT, voucher_id TEXT REFERENCES accounting.vouchers(id),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(book_id,run_key)
);
CREATE TABLE IF NOT EXISTS accounting.periodic_drafts(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES accounting.periodic_runs(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL, kind TEXT NOT NULL,
 description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','approved','posted')),
 version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 approved_by TEXT REFERENCES accounting.users(id), approved_at TEXT, posted_voucher_id TEXT REFERENCES accounting.vouchers(id)
);
CREATE TABLE IF NOT EXISTS accounting.periodic_draft_lines(
 id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES accounting.periodic_drafts(id), line_no INTEGER NOT NULL,
 account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 debit_cents BIGINT NOT NULL CHECK(debit_cents>=0), credit_cents BIGINT NOT NULL CHECK(credit_cents>=0),
 aux_json TEXT NOT NULL DEFAULT '{}', source_ref TEXT NOT NULL DEFAULT '', UNIQUE(draft_id,line_no),
 CHECK((debit_cents>0 AND credit_cents=0) OR (credit_cents>0 AND debit_cents=0)),
 FOREIGN KEY(account_id,account_version) REFERENCES accounting.ledger_account_versions(account_id,version)
);
CREATE TABLE IF NOT EXISTS accounting.report_templates(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, standard TEXT NOT NULL, version INTEGER NOT NULL,
 effective_from TEXT NOT NULL, definition_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounting.report_runs(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL,
 template_id TEXT NOT NULL, template_version INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','validated','reviewed','published','expired','superseded')),
 version INTEGER NOT NULL, inputs_fingerprint TEXT NOT NULL, content_json TEXT NOT NULL, content_hash TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 reviewed_by TEXT REFERENCES accounting.users(id), reviewed_at TEXT,
 published_by TEXT REFERENCES accounting.users(id), published_at TEXT,
 supersedes_id TEXT REFERENCES accounting.report_runs(id),
 UNIQUE(book_id,period,template_id,version)
);
CREATE TABLE IF NOT EXISTS accounting.report_notes(
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES accounting.report_runs(id), note_key TEXT NOT NULL,
 text TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(report_id,note_key)
);
CREATE TABLE IF NOT EXISTS accounting.internal_close_runs(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','preflight_failed','preflight_passed','reviewed','locked','published')),
 version INTEGER NOT NULL, inputs_fingerprint TEXT NOT NULL, checks_json TEXT NOT NULL,
 report_run_id TEXT REFERENCES accounting.report_runs(id), created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL, reviewed_by TEXT REFERENCES accounting.users(id), reviewed_at TEXT,
 locked_by TEXT REFERENCES accounting.users(id), locked_at TEXT, published_by TEXT REFERENCES accounting.users(id), published_at TEXT,
 UNIQUE(book_id,period,version)
);
CREATE TABLE IF NOT EXISTS accounting.internal_period_locks(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL,
 book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL,
 close_run_id TEXT NOT NULL REFERENCES accounting.internal_close_runs(id),
 locked_by TEXT NOT NULL REFERENCES accounting.users(id), locked_at TEXT NOT NULL, UNIQUE(book_id,period)
);

DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['subledger_items','subledger_allocations','subledger_allocation_reversals',
  'bank_statement_lines','bank_recon_adjustments','periodic_rules','periodic_assets','periodic_runs',
  'periodic_drafts','report_runs','internal_close_runs','internal_period_locks'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY %I ON accounting.%I FOR SELECT TO accounting_app USING (accounting.book_allowed(book_id))',t||'_read',t);
  EXECUTE format('CREATE POLICY %I ON accounting.%I FOR INSERT TO accounting_app WITH CHECK (accounting.book_allowed(book_id,true))',t||'_insert',t);
  EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',t);
 END LOOP;
END $$;
DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['bank_statement_lines','bank_recon_adjustments','periodic_runs','periodic_drafts',
  'report_runs','internal_close_runs'] LOOP
  EXECUTE format('CREATE POLICY %I ON accounting.%I FOR UPDATE TO accounting_app USING (accounting.book_allowed(book_id,true)) WITH CHECK (accounting.book_allowed(book_id,true))',t||'_update',t);
 END LOOP;
END $$;
GRANT UPDATE ON accounting.periodic_runs,accounting.periodic_drafts,accounting.report_runs,
 accounting.internal_close_runs,accounting.bank_statement_lines,accounting.bank_recon_adjustments TO accounting_app;
ALTER TABLE accounting.periodic_draft_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.periodic_draft_lines FORCE ROW LEVEL SECURITY;
CREATE POLICY b04_draft_lines_read ON accounting.periodic_draft_lines FOR SELECT TO accounting_app
 USING(EXISTS(SELECT 1 FROM accounting.periodic_drafts d WHERE d.id=periodic_draft_lines.draft_id AND accounting.book_allowed(d.book_id)));
CREATE POLICY b04_draft_lines_insert ON accounting.periodic_draft_lines FOR INSERT TO accounting_app
 WITH CHECK(EXISTS(SELECT 1 FROM accounting.periodic_drafts d WHERE d.id=periodic_draft_lines.draft_id AND accounting.book_allowed(d.book_id,true)));
GRANT SELECT,INSERT ON accounting.periodic_draft_lines TO accounting_app;
GRANT SELECT,INSERT ON accounting.report_templates TO accounting_app;
ALTER TABLE accounting.report_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.report_notes FORCE ROW LEVEL SECURITY;
CREATE POLICY b04_notes_read ON accounting.report_notes FOR SELECT TO accounting_app
 USING(EXISTS(SELECT 1 FROM accounting.report_runs r WHERE r.id=report_notes.report_id AND accounting.book_allowed(r.book_id)));
CREATE POLICY b04_notes_insert ON accounting.report_notes FOR INSERT TO accounting_app
 WITH CHECK(EXISTS(SELECT 1 FROM accounting.report_runs r WHERE r.id=report_notes.report_id AND accounting.book_allowed(r.book_id,true)));
GRANT SELECT,INSERT ON accounting.report_notes TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.b04_append_only_guard() RETURNS TRIGGER LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP IN('UPDATE','DELETE') THEN
  RAISE EXCEPTION 'B04 append-only fact cannot be changed' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.b04_append_only_guard() FROM PUBLIC;
CREATE TRIGGER b04_subledger_items_append BEFORE UPDATE OR DELETE ON accounting.subledger_items FOR EACH ROW EXECUTE FUNCTION accounting.b04_append_only_guard();
CREATE TRIGGER b04_subledger_alloc_append BEFORE UPDATE OR DELETE ON accounting.subledger_allocations FOR EACH ROW EXECUTE FUNCTION accounting.b04_append_only_guard();
CREATE TRIGGER b04_subledger_rev_append BEFORE UPDATE OR DELETE ON accounting.subledger_allocation_reversals FOR EACH ROW EXECUTE FUNCTION accounting.b04_append_only_guard();
CREATE TRIGGER b04_locks_append BEFORE UPDATE OR DELETE ON accounting.internal_period_locks FOR EACH ROW EXECUTE FUNCTION accounting.b04_append_only_guard();

CREATE OR REPLACE FUNCTION accounting.b04_report_published_guard() RETURNS TRIGGER LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF OLD.status='published' AND (
  NEW.content_json<>OLD.content_json OR NEW.content_hash<>OLD.content_hash
  OR NEW.inputs_fingerprint<>OLD.inputs_fingerprint
  OR NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by
  OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
 ) THEN
  RAISE EXCEPTION 'Published report content is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.b04_report_published_guard() FROM PUBLIC;
CREATE TRIGGER b04_report_published_content BEFORE UPDATE ON accounting.report_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.b04_report_published_guard();
