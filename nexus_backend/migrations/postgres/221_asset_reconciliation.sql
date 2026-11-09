-- compatibility: incremental
ALTER TABLE accounting.periodic_runs ADD COLUMN IF NOT EXISTS reconciliation_json TEXT;
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
 SUM(CASE WHEN l.account_id NOT IN(t.expense_account_id,t.accum_account_id) OR l.aux_json<>'{}' THEN 1 ELSE 0 END) AS unsupported
 FROM history h JOIN targets t ON t.id=h.asset_id AND t.target_book_id=h.target_book_id
 JOIN accounting.vouchers v ON v.id=h.voucher_id JOIN accounting.voucher_lines l ON l.voucher_id=v.id
 GROUP BY h.asset_id,h.target_book_id,h.voucher_id,v.period,v.book_id,v.content_fingerprint
)
SELECT t.id AS asset_id,t.target_book_id AS book_id,jsonb_build_object('asset_id',t.id,'book_id',t.target_book_id,'period',t.target_period,'cost_cents',t.cost_cents,'residual_cents',t.residual_cents,'life_months',t.life_months,'start_period',t.start_period,'expense_account_id',t.expense_account_id,'expense_version',t.expense_version,'accum_account_id',t.accum_account_id,'accum_version',t.accum_version,'net_posted_cents',COALESCE((SELECT SUM(net) FROM amounts m WHERE m.asset_id=t.id AND m.target_book_id=t.target_book_id),0),'history',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',m.voucher_id,'period',m.period,'book_id',m.book_id,'fingerprint',m.content_fingerprint,'net_cents',m.net,'accum_cents',m.accum,'unsupported',m.unsupported) ORDER BY m.voucher_id) FROM amounts m WHERE m.asset_id=t.id AND m.target_book_id=t.target_book_id),'[]'::jsonb))::text AS source_json FROM targets t;
GRANT SELECT ON accounting.asset_reconciliation_sources TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.periodic_asset_period_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP='UPDATE' AND (NEW.asset_id,NEW.book_id,NEW.period,NEW.kind,NEW.input_fingerprint)
   IS DISTINCT FROM (OLD.asset_id,OLD.book_id,OLD.period,OLD.kind,OLD.input_fingerprint) THEN
  RAISE EXCEPTION 'Periodic asset run identity is immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.asset_id IS NOT NULL THEN
  IF NOT accounting.book_review_allowed(NEW.book_id) OR NOT accounting.asset_history_allowed(NEW.asset_id,NEW.book_id) OR NOT EXISTS(
   SELECT 1 FROM accounting.periodic_assets a
   JOIN accounting.ledger_periods origin ON origin.book_id=a.book_id
   JOIN accounting.ledger_periods target ON target.book_id=NEW.book_id
   WHERE a.id=NEW.asset_id AND a.kind=NEW.kind AND a.status='active'
    AND origin.ledger_id=target.ledger_id AND origin.company_id=target.company_id
    AND origin.firm_id=target.firm_id AND origin.period<=target.period AND target.period=NEW.period
    AND accounting.book_allowed(a.book_id)
  ) THEN RAISE EXCEPTION 'Invalid asset period or review scope' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM accounting.periodic_runs r WHERE r.asset_id=NEW.asset_id
    AND r.period<>NEW.period AND r.status<>'void' AND (r.period>NEW.period OR r.status<>'posted')) THEN
   RAISE EXCEPTION 'Unfinished or later asset period exists' USING ERRCODE='23514';
  END IF;
  IF TG_OP='INSERT' AND NEW.reconciliation_json IS NULL AND EXISTS(SELECT 1 FROM accounting.periodic_runs r JOIN accounting.vouchers v
    ON v.reversal_of=r.voucher_id WHERE r.asset_id=NEW.asset_id AND r.status='posted') THEN
   RAISE EXCEPTION 'Corrected asset history requires reconciliation' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION accounting.asset_reconciliation_valid(asset TEXT,target TEXT,basis TEXT) RETURNS boolean
 LANGUAGE sql STABLE SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT COALESCE(EXISTS(SELECT 1 FROM accounting.asset_reconciliation_sources s
 JOIN accounting.periodic_assets a ON a.id=s.asset_id JOIN accounting.books b ON b.id=s.book_id
 JOIN accounting.ledger_accounts e ON e.id=a.expense_account_id JOIN accounting.ledger_accounts c ON c.id=a.accum_account_id
 JOIN accounting.ledger_account_versions ev ON ev.account_id=e.id AND ev.version=e.current_version
 JOIN accounting.ledger_account_versions cv ON cv.account_id=c.id AND cv.version=c.current_version
 JOIN accounting.ledger_account_versions eo ON eo.account_id=e.id AND eo.version=a.expense_account_version
 JOIN accounting.ledger_account_versions co ON co.account_id=c.id AND co.version=a.accum_account_version
 WHERE s.asset_id=asset AND s.book_id=target AND s.source_json::jsonb=basis::jsonb
 AND accounting.book_review_allowed(target) AND accounting.asset_history_allowed(asset,target)
 AND a.status='active' AND a.disposed_period IS NULL AND a.start_period<=b.period
 AND NOT EXISTS(SELECT 1 FROM accounting.internal_period_locks WHERE book_id=target AND period=b.period)
 AND (ev.code,ev.category,ev.normal_balance)=(eo.code,eo.category,eo.normal_balance)
 AND (cv.code,cv.category,cv.normal_balance)=(co.code,co.category,co.normal_balance)
 AND ev.is_leaf=1 AND cv.is_leaf=1 AND ev.status='active' AND cv.status='active'
 AND ev.effective_from<=b.period AND (ev.effective_to IS NULL OR ev.effective_to>=b.period)
 AND cv.effective_from<=b.period AND (cv.effective_to IS NULL OR cv.effective_to>=b.period)
 AND (basis::jsonb->>'net_posted_cents')::bigint BETWEEN 0 AND a.cost_cents-a.residual_cents
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(basis::jsonb->'history') h
 WHERE h->>'period'>=b.period OR (h->>'unsupported')::int<>0 OR h->>'net_cents'<>h->>'accum_cents')
 ),false)
$$;
REVOKE ALL ON FUNCTION accounting.asset_reconciliation_valid(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.asset_reconciliation_valid(TEXT,TEXT,TEXT) TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.asset_reconciliation_run_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW.reconciliation_json IS DISTINCT FROM OLD.reconciliation_json OR OLD.status='void' AND NEW IS DISTINCT FROM OLD
   OR OLD.status='posted' AND NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'Reconciliation source and terminal run are immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.status='void' AND (NOT accounting.book_review_allowed(NEW.book_id) OR OLD.status NOT IN('generated','approved')) THEN
   RAISE EXCEPTION 'Reconciliation requires current reviewer and unposted source' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.reconciliation_json IS NOT NULL AND NOT accounting.asset_reconciliation_valid(NEW.asset_id,NEW.book_id,NEW.reconciliation_json) THEN
  RAISE EXCEPTION 'Reconciliation must match current scoped source' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.asset_reconciliation_run_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS asset_reconciliation_run_guard ON accounting.periodic_runs;
CREATE TRIGGER asset_reconciliation_run_guard BEFORE INSERT OR UPDATE ON accounting.periodic_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.asset_reconciliation_run_guard();
CREATE OR REPLACE FUNCTION accounting.asset_reconciliation_review_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE r accounting.periodic_runs; s jsonb; elapsed integer; expected bigint;
BEGIN
 SELECT * INTO r FROM accounting.periodic_runs WHERE id=NEW.run_id;
 IF r.status='void' THEN RAISE EXCEPTION 'Reconciliation replaced this draft' USING ERRCODE='23514'; END IF;
 IF r.reconciliation_json IS NOT NULL AND NEW.status IN('approved','posted') THEN
  s:=r.reconciliation_json::jsonb;
  IF NOT accounting.asset_reconciliation_valid(r.asset_id,r.book_id,r.reconciliation_json) THEN
   RAISE EXCEPTION 'Reconciliation source changed' USING ERRCODE='23514'; END IF;
  elapsed:=(substring(NEW.period,1,4)::int-substring(s->>'start_period',1,4)::int)*12+
   substring(NEW.period,6,2)::int-substring(s->>'start_period',6,2)::int+1;
  expected:=CASE WHEN elapsed >= (s->>'life_months')::int THEN (s->>'cost_cents')::bigint-(s->>'residual_cents')::bigint
   ELSE ((s->>'cost_cents')::bigint-(s->>'residual_cents')::bigint)/(s->>'life_months')::int*elapsed END-(s->>'net_posted_cents')::bigint;
  IF expected<=0 OR (SELECT COUNT(*) FROM accounting.periodic_draft_lines WHERE draft_id=NEW.id)<>2 OR EXISTS(
   SELECT 1 FROM accounting.periodic_draft_lines l WHERE l.draft_id=NEW.id AND NOT (
    l.aux_json::jsonb='{}'::jsonb AND ((l.line_no=1 AND l.account_id=s->>'expense_account_id' AND l.account_version=(s->>'expense_version')::int AND l.debit_cents=expected AND l.credit_cents=0)
    OR (l.line_no=2 AND l.account_id=s->>'accum_account_id' AND l.account_version=(s->>'accum_version')::int AND l.credit_cents=expected AND l.debit_cents=0)))) THEN
   RAISE EXCEPTION 'Reconciliation lines must match reviewed difference' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.asset_reconciliation_review_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS asset_reconciliation_review_guard ON accounting.periodic_drafts;
CREATE TRIGGER asset_reconciliation_review_guard BEFORE UPDATE ON accounting.periodic_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.asset_reconciliation_review_guard();
