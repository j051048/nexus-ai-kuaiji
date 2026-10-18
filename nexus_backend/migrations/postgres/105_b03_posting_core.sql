-- compatibility: incremental
-- B03: immutable formal vouchers, atomic posting commands, numbering,
-- opening binding and reversal relations. No report/month-end engine here.

CREATE TABLE IF NOT EXISTS accounting.opening_bindings(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL, book_id TEXT NOT NULL,
 period TEXT NOT NULL,
 package_version_id TEXT NOT NULL,
 snapshot_fingerprint TEXT NOT NULL CHECK(length(snapshot_fingerprint)=64),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','superseded')),
 bound_by TEXT NOT NULL REFERENCES accounting.users(id), bound_at TEXT NOT NULL,
 UNIQUE(ledger_id,period),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(package_version_id) REFERENCES accounting.opening_package_versions(id)
);

CREATE TABLE IF NOT EXISTS accounting.vouchers(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, ledger_id TEXT NOT NULL, book_id TEXT NOT NULL,
 period TEXT NOT NULL,
 voucher_type TEXT NOT NULL DEFAULT '记' CHECK(voucher_type IN('记','收','付','转')),
 voucher_no INTEGER NOT NULL CHECK(voucher_no>0),
 voucher_date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'posted' CHECK(status='posted'),
 description TEXT NOT NULL CHECK(length(btrim(description)) BETWEEN 1 AND 500),
 source_kind TEXT NOT NULL CHECK(source_kind IN('draft','manual','reversal','adjustment')),
 source_id TEXT, source_version INTEGER,
 opening_binding_id TEXT NOT NULL REFERENCES accounting.opening_bindings(id),
 review_policy_version TEXT NOT NULL,
 content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),
 reversal_of TEXT REFERENCES accounting.vouchers(id),
 relation_kind TEXT CHECK(relation_kind IN('reversal','adjustment')),
 command_id TEXT NOT NULL,
 posted_by TEXT NOT NULL REFERENCES accounting.users(id), posted_at TEXT NOT NULL,
 UNIQUE(book_id,voucher_type,voucher_no),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(opening_binding_id) REFERENCES accounting.opening_bindings(id)
);
CREATE INDEX IF NOT EXISTS idx_vouchers_book_date ON accounting.vouchers(book_id,voucher_date,voucher_type,voucher_no);
CREATE INDEX IF NOT EXISTS idx_vouchers_reversal ON accounting.vouchers(reversal_of);

CREATE TABLE IF NOT EXISTS accounting.voucher_lines(
 id TEXT PRIMARY KEY,
 voucher_id TEXT NOT NULL REFERENCES accounting.vouchers(id),
 line_no INTEGER NOT NULL CHECK(line_no>0),
 account_id TEXT NOT NULL, account_version INTEGER NOT NULL,
 code_snapshot TEXT NOT NULL, name_snapshot TEXT NOT NULL,
 category_snapshot TEXT NOT NULL, normal_balance_snapshot TEXT NOT NULL,
 debit_cents BIGINT NOT NULL CHECK(debit_cents>=0),
 credit_cents BIGINT NOT NULL CHECK(credit_cents>=0),
 aux_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(aux_json::jsonb)='object'),
 source_ref TEXT NOT NULL DEFAULT '',
 UNIQUE(voucher_id,line_no),
 CHECK((debit_cents>0 AND credit_cents=0) OR (credit_cents>0 AND debit_cents=0)),
 FOREIGN KEY(account_id,account_version) REFERENCES accounting.ledger_account_versions(account_id,version)
);
CREATE INDEX IF NOT EXISTS idx_voucher_lines_account ON accounting.voucher_lines(account_id,account_version);

CREATE TABLE IF NOT EXISTS accounting.voucher_sequences(
 book_id TEXT NOT NULL, voucher_type TEXT NOT NULL CHECK(voucher_type IN('记','收','付','转')),
 next_no INTEGER NOT NULL CHECK(next_no>0),
 PRIMARY KEY(book_id,voucher_type),
 FOREIGN KEY(book_id) REFERENCES accounting.books(id)
);

CREATE TABLE IF NOT EXISTS accounting.posting_commands(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 command_key TEXT NOT NULL CHECK(length(command_key) BETWEEN 8 AND 120),
 request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 voucher_id TEXT NOT NULL REFERENCES accounting.vouchers(id),
 result_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(result_json::jsonb)='object'),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(firm_id,book_id,command_key),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);

CREATE TABLE IF NOT EXISTS accounting.voucher_sources(
 id TEXT PRIMARY KEY,
 voucher_id TEXT NOT NULL REFERENCES accounting.vouchers(id),
 source_kind TEXT NOT NULL CHECK(source_kind IN('draft','source','manual')),
 source_id TEXT NOT NULL, source_version INTEGER,
 amount_cents BIGINT NOT NULL CHECK(amount_cents>=0),
 evidence_ref TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_voucher_sources_source ON accounting.voucher_sources(source_kind,source_id);

CREATE TABLE IF NOT EXISTS accounting.voucher_relations(
 id TEXT PRIMARY KEY,
 original_voucher_id TEXT NOT NULL REFERENCES accounting.vouchers(id),
 related_voucher_id TEXT NOT NULL UNIQUE REFERENCES accounting.vouchers(id),
 kind TEXT NOT NULL CHECK(kind IN('reversal','adjustment')),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_voucher_full_reversal
 ON accounting.voucher_relations(original_voucher_id) WHERE kind='reversal';

ALTER TABLE accounting.opening_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.opening_bindings FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.vouchers ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.vouchers FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_lines FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_sequences FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.posting_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.posting_commands FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_sources FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.voucher_relations FORCE ROW LEVEL SECURITY;

CREATE POLICY b03_bindings_read ON accounting.opening_bindings FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY b03_bindings_insert ON accounting.opening_bindings FOR INSERT TO accounting_app
 WITH CHECK(accounting.book_allowed(book_id,true));
CREATE POLICY b03_vouchers_read ON accounting.vouchers FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY b03_vouchers_insert ON accounting.vouchers FOR INSERT TO accounting_app
 WITH CHECK(accounting.book_allowed(book_id,true));
CREATE POLICY b03_lines_read ON accounting.voucher_lines FOR SELECT TO accounting_app
 USING(EXISTS(SELECT 1 FROM accounting.vouchers v WHERE v.id=voucher_lines.voucher_id AND accounting.book_allowed(v.book_id)));
CREATE POLICY b03_lines_insert ON accounting.voucher_lines FOR INSERT TO accounting_app
 WITH CHECK(EXISTS(SELECT 1 FROM accounting.vouchers v WHERE v.id=voucher_lines.voucher_id AND accounting.book_allowed(v.book_id,true)));
CREATE POLICY b03_sequences_read ON accounting.voucher_sequences FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY b03_sequences_write ON accounting.voucher_sequences FOR ALL TO accounting_app
 USING(accounting.book_allowed(book_id,true)) WITH CHECK(accounting.book_allowed(book_id,true));
CREATE POLICY b03_commands_read ON accounting.posting_commands FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY b03_commands_insert ON accounting.posting_commands FOR INSERT TO accounting_app
 WITH CHECK(accounting.book_allowed(book_id,true));
CREATE POLICY b03_sources_read ON accounting.voucher_sources FOR SELECT TO accounting_app
 USING(EXISTS(SELECT 1 FROM accounting.vouchers v WHERE v.id=voucher_sources.voucher_id AND accounting.book_allowed(v.book_id)));
CREATE POLICY b03_sources_insert ON accounting.voucher_sources FOR INSERT TO accounting_app
 WITH CHECK(EXISTS(SELECT 1 FROM accounting.vouchers v WHERE v.id=voucher_sources.voucher_id AND accounting.book_allowed(v.book_id,true)));
CREATE POLICY b03_relations_read ON accounting.voucher_relations FOR SELECT TO accounting_app
 USING(EXISTS(SELECT 1 FROM accounting.vouchers v WHERE v.id=voucher_relations.original_voucher_id AND accounting.book_allowed(v.book_id)));
CREATE POLICY b03_relations_insert ON accounting.voucher_relations FOR INSERT TO accounting_app
 WITH CHECK(EXISTS(SELECT 1 FROM accounting.vouchers v WHERE v.id=voucher_relations.original_voucher_id AND accounting.book_allowed(v.book_id,true)));

REVOKE ALL ON accounting.opening_bindings,accounting.vouchers,accounting.voucher_lines,
 accounting.voucher_sequences,accounting.posting_commands,accounting.voucher_sources,
 accounting.voucher_relations FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT ON accounting.opening_bindings,accounting.vouchers,accounting.voucher_lines,
 accounting.posting_commands,accounting.voucher_sources,accounting.voucher_relations TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.voucher_sequences TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.b03_immutable_voucher_guard()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP IN('UPDATE','DELETE') THEN
  RAISE EXCEPTION 'Formal vouchers and lines are append-only' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.b03_immutable_voucher_guard() FROM PUBLIC;
CREATE TRIGGER b03_voucher_immutable BEFORE UPDATE OR DELETE ON accounting.vouchers
 FOR EACH ROW EXECUTE FUNCTION accounting.b03_immutable_voucher_guard();
CREATE TRIGGER b03_voucher_line_immutable BEFORE UPDATE OR DELETE ON accounting.voucher_lines
 FOR EACH ROW EXECUTE FUNCTION accounting.b03_immutable_voucher_guard();
CREATE TRIGGER b03_voucher_relation_immutable BEFORE UPDATE OR DELETE ON accounting.voucher_relations
 FOR EACH ROW EXECUTE FUNCTION accounting.b03_immutable_voucher_guard();
