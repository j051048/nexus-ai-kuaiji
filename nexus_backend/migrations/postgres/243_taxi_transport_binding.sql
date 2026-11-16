-- compatibility: incremental
-- Existing batches lack trusted transport identity and must be regenerated.
ALTER TABLE accounting.tax_fee_batches ADD COLUMN IF NOT EXISTS transport_snapshot_json TEXT NOT NULL DEFAULT '[]';

CREATE OR REPLACE FUNCTION accounting.taxi_transport_draft_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b accounting.tax_fee_batches; s JSONB; a JSONB; expected_aux JSONB;
BEGIN
 IF NEW.status NOT IN('approved','exported') OR NEW.status=OLD.status THEN RETURN NEW; END IF;
 SELECT * INTO b FROM accounting.tax_fee_batches WHERE draft_id=NEW.id AND book_id=NEW.book_id;
 IF NOT FOUND THEN RETURN NEW; END IF;
 IF jsonb_typeof(b.transport_snapshot_json::jsonb)<>'array' OR jsonb_array_length(b.transport_snapshot_json::jsonb)=0 THEN
  RAISE EXCEPTION 'Taxi fee transport snapshot required' USING ERRCODE='23514';
 END IF;
 FOR s IN SELECT value FROM jsonb_array_elements(b.transport_snapshot_json::jsonb) LOOP
  IF NOT EXISTS(SELECT 1 FROM accounting.transport_subjects x WHERE x.id=s->'driver'->>'id' AND x.firm_id=b.firm_id AND x.company_id=b.company_id AND x.active AND to_jsonb(x) @> (s->'driver'))
   OR NOT EXISTS(SELECT 1 FROM accounting.transport_subjects x WHERE x.id=s->'vehicle'->>'id' AND x.firm_id=b.firm_id AND x.company_id=b.company_id AND x.active AND to_jsonb(x) @> (s->'vehicle'))
   OR NOT EXISTS(SELECT 1 FROM accounting.company_master_entities x WHERE x.id=s->'counterparty'->>'id' AND x.firm_id=b.firm_id AND x.company_id=b.company_id AND x.active=1 AND to_jsonb(x) @> (s->'counterparty'))
   OR NOT EXISTS(SELECT 1 FROM accounting.company_master_entities x WHERE x.id=s->'project'->>'id' AND x.firm_id=b.firm_id AND x.company_id=b.company_id AND x.active=1 AND to_jsonb(x) @> (s->'project'))
   OR NOT EXISTS(SELECT 1 FROM accounting.transport_relations x WHERE x.id=s->'driver_relation'->>'id' AND x.firm_id=b.firm_id AND x.company_id=b.company_id AND x.status='active' AND to_jsonb(x) @> (s->'driver_relation'))
   OR NOT EXISTS(SELECT 1 FROM accounting.transport_relations x WHERE x.id=s->'vehicle_relation'->>'id' AND x.firm_id=b.firm_id AND x.company_id=b.company_id AND x.status='active' AND to_jsonb(x) @> (s->'vehicle_relation'))
   OR NOT EXISTS(SELECT 1 FROM accounting.transport_assignments x WHERE x.id=s->'assignment'->>'id' AND x.firm_id=b.firm_id AND x.company_id=b.company_id AND to_jsonb(x) @> (s->'assignment'))
  THEN RAISE EXCEPTION 'Taxi fee transport identity or mapping changed' USING ERRCODE='23514'; END IF;
  FOR a IN SELECT to_jsonb(x) FROM accounting.draft_line_sources x WHERE x.draft_id=NEW.id AND x.book_id=NEW.book_id
   AND x.source_file_id=s->>'source_file_id' AND x.source_row_number=(s->>'source_row_number')::integer LOOP
   expected_aux:=CASE a->>'role' WHEN 'receivable' THEN jsonb_build_object('counterparty',s->'counterparty'->>'id') ELSE jsonb_build_object('project',s->'project'->>'id') END;
   IF a->>'driver_id'<>s->'driver'->>'id' OR a->>'vehicle_id'<>s->'vehicle'->>'id' OR NOT EXISTS(
     SELECT 1 FROM accounting.draft_line_auxiliaries x WHERE x.draft_id=NEW.id AND x.book_id=NEW.book_id
      AND x.ordinal=(a->>'ordinal')::integer AND x.values_json::jsonb=expected_aux)
   THEN RAISE EXCEPTION 'Taxi fee auxiliary values differ from frozen identity' USING ERRCODE='23514'; END IF;
  END LOOP;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.taxi_transport_draft_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS taxi_transport_draft_guard ON accounting.drafts;
CREATE TRIGGER taxi_transport_draft_guard BEFORE UPDATE ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.taxi_transport_draft_guard();
