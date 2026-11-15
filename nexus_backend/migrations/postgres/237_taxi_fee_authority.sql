-- compatibility: incremental
-- A-D audit: a visible company is not authority to approve its drafts.
ALTER TABLE accounting.tax_source_files ALTER COLUMN amount_total_cents TYPE BIGINT;
ALTER TABLE accounting.tax_source_files ALTER COLUMN quantity_milli TYPE BIGINT;
CREATE INDEX IF NOT EXISTS taxi_fee_draft_idx ON accounting.tax_fee_batches(draft_id,book_id);
DROP POLICY IF EXISTS tax_fee_batches_tenant ON accounting.tax_fee_batches;
DROP POLICY IF EXISTS taxi_fee_read ON accounting.tax_fee_batches;
CREATE POLICY taxi_fee_read ON accounting.tax_fee_batches FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
DROP POLICY IF EXISTS taxi_fee_insert ON accounting.tax_fee_batches;
CREATE POLICY taxi_fee_insert ON accounting.tax_fee_batches FOR INSERT TO accounting_app
 WITH CHECK(accounting.book_review_allowed(book_id) AND accounting.firm_manager(firm_id));
DROP POLICY IF EXISTS taxi_fee_update ON accounting.tax_fee_batches;
CREATE POLICY taxi_fee_update ON accounting.tax_fee_batches FOR UPDATE TO accounting_app
 USING(accounting.book_review_allowed(book_id)) WITH CHECK(accounting.book_review_allowed(book_id));
DROP POLICY IF EXISTS tax_fee_batch_rows_tenant ON accounting.tax_fee_batch_rows;
DROP POLICY IF EXISTS taxi_fee_rows_read ON accounting.tax_fee_batch_rows;
CREATE POLICY taxi_fee_rows_read ON accounting.tax_fee_batch_rows FOR SELECT TO accounting_app
 USING(EXISTS(SELECT 1 FROM accounting.tax_fee_batches b WHERE b.id=batch_id AND accounting.book_allowed(b.book_id)));
DROP POLICY IF EXISTS taxi_fee_rows_insert ON accounting.tax_fee_batch_rows;
CREATE POLICY taxi_fee_rows_insert ON accounting.tax_fee_batch_rows FOR INSERT TO accounting_app
 WITH CHECK(EXISTS(SELECT 1 FROM accounting.tax_fee_batches b WHERE b.id=batch_id AND b.status='draft' AND accounting.book_review_allowed(b.book_id) AND accounting.firm_manager(b.firm_id)));
DROP POLICY IF EXISTS draft_line_sources_tenant ON accounting.draft_line_sources;
DROP POLICY IF EXISTS taxi_line_read ON accounting.draft_line_sources;
CREATE POLICY taxi_line_read ON accounting.draft_line_sources FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
DROP POLICY IF EXISTS taxi_line_insert ON accounting.draft_line_sources;
CREATE POLICY taxi_line_insert ON accounting.draft_line_sources FOR INSERT TO accounting_app
 WITH CHECK(accounting.book_review_allowed(book_id) AND EXISTS(SELECT 1 FROM accounting.drafts d WHERE d.id=draft_id AND d.book_id=draft_line_sources.book_id AND d.status='draft'));
DROP POLICY IF EXISTS tax_source_files_read ON accounting.tax_source_files;
CREATE POLICY tax_source_files_read ON accounting.tax_source_files FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));

CREATE OR REPLACE FUNCTION accounting.taxi_fee_state_guard() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.drafts d ON d.book_id=b.id
   WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id
     AND b.period=NEW.period AND d.id=NEW.draft_id
     AND EXISTS(SELECT 1 FROM accounting.periods p WHERE p.book_id=b.id AND p.period=b.period AND p.status='open')
     AND NOT EXISTS(SELECT 1 FROM accounting.internal_period_locks l WHERE l.book_id=b.id))
 THEN RAISE EXCEPTION 'Taxi fee scope or period invalid' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.status<>'draft' OR NEW.version<>1 OR NEW.created_by<>accounting.actor_user()
      OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
   THEN RAISE EXCEPTION 'Taxi fee must start as an attributed draft' USING ERRCODE='23514'; END IF;
 ELSE
   IF (to_jsonb(NEW)-ARRAY['status','version','reviewed_by','reviewed_at','source_snapshot_json'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','version','reviewed_by','reviewed_at','source_snapshot_json'])
      OR (OLD.source_snapshot_json<>'[]' AND NEW.source_snapshot_json<>OLD.source_snapshot_json)
   THEN RAISE EXCEPTION 'Taxi fee frozen identity is immutable' USING ERRCODE='23514'; END IF;
   IF NEW.status<>OLD.status AND (OLD.status='invalidated' OR NEW.version<>OLD.version+1)
   THEN RAISE EXCEPTION 'Taxi fee terminal state or version invalid' USING ERRCODE='23514'; END IF;
   IF NEW.status='reviewed' AND OLD.status<>'reviewed' AND NOT EXISTS(
      SELECT 1 FROM accounting.drafts d JOIN accounting.effective_draft_review_receipts r
        ON r.draft_id=d.id AND r.book_id=d.book_id AND r.reviewed_version=d.version-1
      WHERE d.id=NEW.draft_id AND d.book_id=NEW.book_id AND d.status='approved'
        AND r.independent=1 AND r.reviewer_user_id=accounting.actor_user()
        AND NEW.reviewed_by=r.reviewer_user_id AND NEW.reviewed_by<>NEW.created_by
        AND NEW.reviewed_at IS NOT NULL)
   THEN RAISE EXCEPTION 'Independent draft review evidence required' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.taxi_fee_state_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS taxi_fee_state_guard ON accounting.tax_fee_batches;
CREATE TRIGGER taxi_fee_state_guard BEFORE INSERT OR UPDATE ON accounting.tax_fee_batches
 FOR EACH ROW EXECUTE FUNCTION accounting.taxi_fee_state_guard();

CREATE OR REPLACE FUNCTION accounting.taxi_fee_draft_guard() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b accounting.tax_fee_batches; snap JSONB;
BEGIN
 IF NEW.status NOT IN('approved','exported') OR NEW.status=OLD.status THEN RETURN NEW; END IF;
 SELECT * INTO b FROM accounting.tax_fee_batches WHERE draft_id=NEW.id AND book_id=NEW.book_id;
 IF NOT FOUND THEN RETURN NEW; END IF;
 snap:=b.source_snapshot_json::jsonb;
 IF b.status='invalidated' OR jsonb_array_length(snap)=0 OR EXISTS(
   SELECT 1 FROM jsonb_array_elements(snap) s WHERE NOT EXISTS(
    SELECT 1 FROM accounting.tax_source_files f WHERE f.id=s->>0 AND f.fingerprint=s->>1
     AND f.book_id=b.book_id AND f.firm_id=b.firm_id AND f.company_id=b.company_id AND f.status='active'
     AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(f.rows_json::jsonb) r WHERE r->>'status'='needs_review')))
   OR EXISTS(SELECT 1 FROM accounting.tax_fee_batch_rows WHERE batch_id=b.id AND status='rejected')
 THEN RAISE EXCEPTION 'Taxi fee source is stale, incomplete or invalidated' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.taxi_fee_draft_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS taxi_fee_draft_guard ON accounting.drafts;
CREATE TRIGGER taxi_fee_draft_guard BEFORE UPDATE ON accounting.drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.taxi_fee_draft_guard();
