-- compatibility: incremental
-- Allow asset_id in voucher_lines.aux_json when computing asset reconciliation
-- history, matching app.periodic.reconciliation._unsupported_history while
-- continuing to block any other auxiliary key or foreign account.
SET LOCAL search_path=accounting,pg_catalog;

CREATE OR REPLACE VIEW accounting.asset_reconciliation_sources WITH (security_invoker=true) AS
WITH RECURSIVE targets AS (
 SELECT a.*, t.book_id AS target_book_id,t.period AS target_period,
 e.current_version AS expense_version,c.current_version AS accum_version
 FROM accounting.periodic_assets a JOIN accounting.ledger_periods o ON o.book_id=a.book_id
 JOIN accounting.ledger_periods t ON t.ledger_id=o.ledger_id AND t.company_id=o.company_id AND t.period>=o.period
 JOIN accounting.ledger_accounts e ON e.id=a.expense_account_id JOIN accounting.ledger_accounts c ON c.id=a.accum_account_id
), history(asset_id,target_book_id,voucher_id) AS (
 SELECT t.id,t.target_book_id,r.voucher_id FROM targets t JOIN accounting.periodic_runs r ON r.asset_id=t.id
 WHERE r.status='posted' AND r.period<t.target_period AND r.voucher_id IS NOT NULL
 UNION
 SELECT h.asset_id,h.target_book_id,v.id FROM history h JOIN accounting.vouchers v ON v.reversal_of=h.voucher_id
), amounts AS (
 SELECT h.asset_id,h.target_book_id,h.voucher_id,v.period,v.book_id,v.content_fingerprint,
 COALESCE(SUM(CASE WHEN l.account_id=t.expense_account_id THEN l.debit_cents-l.credit_cents ELSE 0 END),0) AS net,
 COALESCE(SUM(CASE WHEN l.account_id=t.accum_account_id THEN l.credit_cents-l.debit_cents ELSE 0 END),0) AS accum,
 SUM(CASE WHEN l.account_id NOT IN(t.expense_account_id,t.accum_account_id) OR (COALESCE(NULLIF(l.aux_json,''),'{}')::jsonb - 'asset_id')<>'{}'::jsonb THEN 1 ELSE 0 END) AS unsupported
 FROM history h JOIN targets t ON t.id=h.asset_id AND t.target_book_id=h.target_book_id
 JOIN accounting.vouchers v ON v.id=h.voucher_id JOIN accounting.voucher_lines l ON l.voucher_id=v.id
 GROUP BY h.asset_id,h.target_book_id,h.voucher_id,v.period,v.book_id,v.content_fingerprint
)
SELECT t.id AS asset_id,t.target_book_id AS book_id,jsonb_build_object('asset_id',t.id,'book_id',t.target_book_id,'period',t.target_period,'cost_cents',t.cost_cents,'residual_cents',t.residual_cents,'life_months',t.life_months,'start_period',t.start_period,'expense_account_id',t.expense_account_id,'expense_version',t.expense_version,'accum_account_id',t.accum_account_id,'accum_version',t.accum_version,'net_posted_cents',COALESCE((SELECT SUM(net) FROM amounts m WHERE m.asset_id=t.id AND m.target_book_id=t.target_book_id),0),'history',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',m.voucher_id,'period',m.period,'book_id',m.book_id,'fingerprint',m.content_fingerprint,'net_cents',m.net,'accum_cents',m.accum,'unsupported',m.unsupported) ORDER BY m.voucher_id) FROM amounts m WHERE m.asset_id=t.id AND m.target_book_id=t.target_book_id),'[]'::jsonb))::text AS source_json FROM targets t;
GRANT SELECT ON accounting.asset_reconciliation_sources TO accounting_app;
