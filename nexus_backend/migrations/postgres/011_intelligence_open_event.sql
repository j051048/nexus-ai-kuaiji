-- Staff can know whether their own event is still open for processing without
-- opening the ledger mapping, book, draft identity or any ledger balance.
CREATE FUNCTION accounting.ai_event_open(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_event_visible(target_event,target_firm,target_company)
 AND NOT EXISTS(SELECT 1 FROM accounting.business_mappings m
  JOIN accounting.drafts d ON d.id=m.draft_id AND d.book_id=m.book_id
  WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company AND d.status<>'draft')
$$;
REVOKE ALL ON FUNCTION accounting.ai_event_open(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.ai_event_open(TEXT,TEXT,TEXT) TO accounting_app;
