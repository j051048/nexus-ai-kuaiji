-- compatibility: incremental
-- ledger_account_versions.is_leaf is INTEGER in both accounting dialects.
CREATE OR REPLACE FUNCTION accounting.c01_evidence_reference_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE scope_book RECORD; candidate JSONB; entry RECORD;
BEGIN
 candidate:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 SELECT firm_id,company_id INTO scope_book FROM accounting.books WHERE id=candidate->>'book_id';
 IF NOT FOUND OR NOT accounting.book_review_allowed(candidate->>'book_id') THEN RAISE EXCEPTION 'Current C01 reviewer required' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(scope_book.firm_id,scope_book.company_id);
 IF TG_TABLE_NAME='bank_statement_lines' THEN
  IF TG_OP IN('UPDATE','DELETE') THEN
   IF EXISTS(SELECT 1 FROM accounting.bank_adjustment_resolutions WHERE bank_line_id=OLD.id) THEN RAISE EXCEPTION 'Resolved bank evidence is immutable' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.created_by IS DISTINCT FROM accounting.actor_user() THEN RAISE EXCEPTION 'Current bank statement author required' USING ERRCODE='23514'; END IF;
  END IF;
 ELSIF TG_TABLE_NAME='bank_accounts' THEN
  IF TG_OP='UPDATE' THEN
   IF EXISTS(SELECT 1 FROM accounting.c01_approved_banks WHERE bank_account_id=OLD.id)
   AND (NEW.book_id,NEW.firm_id,NEW.company_id,NEW.ledger_account_id,NEW.ledger_account_version,NEW.aux_json::jsonb)
    IS DISTINCT FROM (OLD.book_id,OLD.firm_id,OLD.company_id,OLD.ledger_account_id,OLD.ledger_account_version,OLD.aux_json::jsonb)
   THEN RAISE EXCEPTION 'Reviewed bank identity mapping is immutable' USING ERRCODE='23514'; END IF;
  END IF;
  IF TG_OP<>'DELETE' THEN
   IF jsonb_typeof(NEW.aux_json::jsonb) IS DISTINCT FROM 'object' OR NOT EXISTS(
    SELECT 1 FROM accounting.ledger_accounts la JOIN accounting.ledger_account_versions av ON av.account_id=la.id AND av.version=NEW.ledger_account_version
    WHERE la.id=NEW.ledger_account_id AND av.is_leaf=1 AND av.category='asset'
    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(av.auxiliary_requirements::jsonb) needed(key) WHERE NOT NEW.aux_json::jsonb ? needed.key))
   THEN RAISE EXCEPTION 'Bank account requires exact leaf asset auxiliary mapping' USING ERRCODE='23514'; END IF;
   FOR entry IN SELECT * FROM jsonb_each(NEW.aux_json::jsonb) LOOP
    IF jsonb_typeof(entry.value)<>'string' OR NOT accounting.c01_subledger_aux_valid(NEW.book_id,entry.key,entry.value#>>'{}') THEN RAISE EXCEPTION 'Bank auxiliary entity invalid' USING ERRCODE='23514'; END IF;
   END LOOP;
  END IF;
 ELSIF TG_TABLE_NAME='subledger_allocation_requests' THEN
  IF NEW.created_by IS DISTINCT FROM accounting.actor_user() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
   OR NEW.firm_id IS DISTINCT FROM scope_book.firm_id OR NEW.company_id IS DISTINCT FROM scope_book.company_id
   OR NOT EXISTS(SELECT 1 FROM accounting.vouchers WHERE id=NEW.voucher_id AND book_id=NEW.book_id)
  THEN RAISE EXCEPTION 'Allocation request scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
