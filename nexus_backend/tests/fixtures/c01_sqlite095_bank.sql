-- Synthetic SQLite 095 history. No current service, fake permission view,
-- disabled trigger or invented independent-review receipt is used here.
INSERT INTO users(id,name) VALUES('legacy-owner','历史制单人');
INSERT INTO firms(id,name) VALUES('legacy-firm','虚构历史事务所');
INSERT INTO memberships(firm_id,user_id,role) VALUES('legacy-firm','legacy-owner','owner');
INSERT INTO companies(id,name) VALUES('legacy-company','虚构历史企业');
INSERT INTO firm_clients(firm_id,company_id) VALUES('legacy-firm','legacy-company');
INSERT INTO books(id,firm_id,company_id,name,period) VALUES('legacy-book','legacy-firm','legacy-company','历史十月','2026-10');
INSERT INTO periods(book_id,period) VALUES('legacy-book','2026-10');
INSERT INTO ledgers(id,firm_id,company_id,name,created_by,created_at)
 VALUES('legacy-ledger','legacy-firm','legacy-company','历史核算主体','legacy-owner','2026-10-01');
INSERT INTO ledger_periods(ledger_id,book_id,firm_id,company_id,period,linked_by,linked_at)
 VALUES('legacy-ledger','legacy-book','legacy-firm','legacy-company','2026-10','legacy-owner','2026-10-01');
INSERT INTO accounting_policies(id,firm_id,company_id,ledger_id,standard_kind,taxpayer_kind,fiscal_year_start_month,version,status,effective_from,reason,created_by,created_at)
 VALUES('legacy-policy','legacy-firm','legacy-company','legacy-ledger','small_enterprise','small_scale',1,1,'active','2026-01','历史政策','legacy-owner','2026-10-01');
INSERT INTO ledger_accounts(id,firm_id,company_id,ledger_id,current_version,created_by,created_at)
 VALUES('legacy-bank','legacy-firm','legacy-company','legacy-ledger',1,'legacy-owner','2026-10-01'),
 ('legacy-payable','legacy-firm','legacy-company','legacy-ledger',1,'legacy-owner','2026-10-01');
INSERT INTO ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,code,name,level,is_leaf,category,normal_balance,effective_from,reason,created_by,created_at)
 VALUES('legacy-bank-v1','legacy-bank','legacy-ledger','legacy-firm','legacy-company',1,'1002','银行存款',1,1,'asset','debit','2026-01','历史科目','legacy-owner','2026-10-01'),
 ('legacy-payable-v1','legacy-payable','legacy-ledger','legacy-firm','legacy-company',1,'2202','应付账款',1,1,'liability','credit','2026-01','历史科目','legacy-owner','2026-10-01');
INSERT INTO opening_packages(id,firm_id,company_id,ledger_id,target_period,policy_id,status,created_by,created_at,updated_at)
 VALUES('legacy-opening','legacy-firm','legacy-company','legacy-ledger','2026-10','legacy-policy','frozen','legacy-owner','2026-10-01','2026-10-01');
INSERT INTO opening_package_versions(id,package_id,version,policy_version,config_fingerprint,status,request_key,request_fingerprint,created_by,created_at)
 VALUES('legacy-opening-v1','legacy-opening',1,1,'legacy-config','draft','legacy-opening','legacy-request','legacy-owner','2026-10-01');
INSERT INTO opening_lines(id,package_version_id,line_no,account_id,account_version,year_opening_cents,ytd_debit_cents,ytd_credit_cents,opening_cents)
 VALUES('legacy-opening-bank','legacy-opening-v1',1,'legacy-bank',1,1000000,0,0,1000000),
 ('legacy-opening-payable','legacy-opening-v1',2,'legacy-payable',1,-1000000,0,0,-1000000);
UPDATE opening_package_versions SET status='frozen' WHERE id='legacy-opening-v1';
INSERT INTO opening_bindings(id,firm_id,company_id,ledger_id,book_id,period,package_version_id,snapshot_fingerprint,bound_by,bound_at)
 VALUES('legacy-binding','legacy-firm','legacy-company','legacy-ledger','legacy-book','2026-10','legacy-opening-v1','legacy-config','legacy-owner','2026-10-01');
INSERT INTO vouchers(id,firm_id,company_id,ledger_id,book_id,period,voucher_no,voucher_date,description,source_kind,opening_binding_id,review_policy_version,content_fingerprint,command_id,posted_by,posted_at)
 VALUES('legacy-voucher','legacy-firm','legacy-company','legacy-ledger','legacy-book','2026-10',1,'2026-10-20','清偿期初','manual','legacy-binding','legacy-before-review-policy','legacy-voucher-content','legacy-command','legacy-owner','2026-10-20');
INSERT INTO voucher_lines(id,voucher_id,line_no,account_id,account_version,code_snapshot,name_snapshot,category_snapshot,normal_balance_snapshot,debit_cents,credit_cents)
 VALUES('legacy-payment','legacy-voucher',1,'legacy-payable',1,'2202','应付账款','liability','credit',1000000,0),
 ('legacy-bank-line','legacy-voucher',2,'legacy-bank',1,'1002','银行存款','asset','debit',0,1000000);
INSERT INTO posting_commands(id,firm_id,company_id,book_id,command_key,request_fingerprint,voucher_id,created_by,created_at)
 VALUES('legacy-command','legacy-firm','legacy-company','legacy-book','empty-bank-balance','legacy-request','legacy-voucher','legacy-owner','2026-10-20');
INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,amount_cents,matched_voucher_line_id,status,created_by,created_at)
 VALUES('legacy-statement','legacy-book','2026-10','2026-10-20','合成清偿流水',-1000000,'legacy-bank-line','matched','legacy-owner','2026-10-20');
INSERT INTO bank_recon_adjustments(id,book_id,period,kind,amount_cents,explanation,status,created_by,created_at)
 VALUES('legacy-positive','legacy-book','2026-10','other',100,'分别未解决的调整项','open','legacy-owner','2026-10-21'),
 ('legacy-negative','legacy-book','2026-10','other',-100,'分别未解决的调整项','open','legacy-owner','2026-10-21');
