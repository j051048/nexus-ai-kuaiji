-- A closed/missing canonical accounting period also freezes model processing.
CREATE OR REPLACE FUNCTION accounting.ai_event_open(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_event_visible(target_event,target_firm,target_company)
 AND NOT EXISTS(SELECT 1 FROM accounting.business_mappings m
  JOIN accounting.drafts d ON d.id=m.draft_id AND d.book_id=m.book_id
  JOIN accounting.books b ON b.id=m.book_id AND b.firm_id=m.firm_id AND b.company_id=m.company_id
  LEFT JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
  WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company
  AND (d.status<>'draft' OR p.status IS DISTINCT FROM 'open'))
$$;
REVOKE ALL ON FUNCTION accounting.ai_event_open(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.ai_event_open(TEXT,TEXT,TEXT) TO accounting_app;
