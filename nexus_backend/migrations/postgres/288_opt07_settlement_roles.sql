-- OPT07-D: clearing accounts are configured per book, not hard-coded.
DO $$
DECLARE cname TEXT;
BEGIN
  SELECT conname INTO cname FROM pg_constraint
   WHERE conrelid='accounting.settlement_items'::regclass AND contype='c'
     AND pg_get_constraintdef(oid) LIKE '%clearing_account%';
  IF cname IS NOT NULL THEN
    EXECUTE format('ALTER TABLE accounting.settlement_items DROP CONSTRAINT %I', cname);
  END IF;
END $$;
ALTER TABLE accounting.settlement_items
  ADD CONSTRAINT settlement_items_clearing_account_check
  CHECK (length(btrim(clearing_account)) > 0);

CREATE OR REPLACE FUNCTION accounting.settlement_clearing_account(target_book TEXT,target_source TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE s RECORD; c TEXT; n INTEGER; total BIGINT; role_name TEXT; BEGIN
 SELECT * INTO s FROM accounting.sources WHERE id=target_source AND book_id=target_book AND source_kind='business';IF NOT FOUND THEN RETURN NULL;END IF;
 role_name := CASE WHEN s.direction='expense' THEN 'payable' ELSE 'receivable' END;
 SELECT min(account_code),count(*),sum(amount)::bigint INTO c,n,total FROM (
  SELECT l.account_code,sum(CASE WHEN s.direction='expense' THEN credit_cents ELSE debit_cents END) AS amount
   FROM accounting.draft_lines l JOIN accounting.drafts d ON d.id=l.draft_id AND d.book_id=l.book_id
   JOIN accounting.account_roles r ON r.book_id=target_book AND r.code=l.account_code AND r.role=role_name
   WHERE d.source_id=target_source AND d.book_id=target_book
   GROUP BY l.account_code HAVING sum(CASE WHEN s.direction='expense' THEN credit_cents ELSE debit_cents END)>0
 ) codes;
 IF n<>1 OR total<>s.amount_cents THEN RETURN NULL; END IF;RETURN c;END $$;
