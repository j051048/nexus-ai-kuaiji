-- compatibility: incremental
-- A committed result must belong to the frozen object/version selection.
CREATE OR REPLACE FUNCTION accounting.dcp_batch_committed_result(target_item TEXT) RETURNS JSONB
 LANGUAGE sql STABLE SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT CASE b.kind WHEN 'review_export' THEN
  (SELECT jsonb_build_object('status','created','book_id',e.book_id,'export_id',e.id,'filename',e.filename,'count',e.count)
   FROM accounting.exports e JOIN accounting.books bk ON bk.id=e.book_id
   JOIN accounting.delivery_manifests m ON m.export_id=e.id AND m.book_id=e.book_id
   WHERE e.idempotency_key='batch:'||b.id||':'||i.company_id AND e.created_by=b.created_by
    AND bk.company_id=i.company_id AND bk.firm_id=i.firm_id AND e.book_id=entry.value->>'book_id'
    AND (SELECT jsonb_agg(jsonb_build_object('draft_id',v->>'draft_id','version',(v->>'approved_version')::integer) ORDER BY v->>'draft_id')
     FROM jsonb_array_elements(m.payload_json::jsonb->'items') v) = entry.value->'drafts')
 WHEN 'package_draft' THEN
  (SELECT jsonb_build_object('status','created','book_id',p.book_id,'package_id',p.id,'package_version',p.version,'report_id',p.report_id,'report_version',p.report_version)
   FROM accounting.monthly_delivery_packages p WHERE p.idempotency_key='batch:'||b.id||':'||i.company_id
    AND p.created_by=b.created_by AND p.company_id=i.company_id AND p.firm_id=i.firm_id
    AND p.book_id=entry.value->>'book_id' AND p.report_id=entry.value->>'report_id'
    AND p.report_version=(entry.value->>'report_version')::integer)
 ELSE NULL END FROM accounting.batch_operation_items i JOIN accounting.batch_operations b ON b.id=i.batch_id
 JOIN LATERAL jsonb_array_elements(COALESCE(b.request_json::jsonb->'companies','[]'::jsonb)) entry(value)
  ON CASE WHEN jsonb_typeof(entry.value)='object' THEN entry.value->>'company_id' END=i.company_id
 WHERE i.id=target_item $$;
REVOKE ALL ON FUNCTION accounting.dcp_batch_committed_result(TEXT) FROM PUBLIC,accounting_app,accounting_worker;
