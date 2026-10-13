-- A legacy unknown account cannot hide a physical transaction after namespace
-- adoption. Two legacy records preserve their existing import behavior.
CREATE OR REPLACE FUNCTION accounting.bank_source_import_blocked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book)
   THEN RAISE EXCEPTION 'Bank source scope denied' USING ERRCODE='42501'; END IF;
  RETURN EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.sources o ON o.book_id=s.book_id AND o.id<>s.id AND o.source_kind='bank'
   LEFT JOIN accounting.bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id
   LEFT JOIN accounting.bank_import_keys ko ON ko.source_id=o.id AND ko.book_id=o.book_id
   WHERE s.id=target_source AND s.book_id=target_book AND s.source_kind='bank'
    AND s.date=o.date AND s.direction=o.direction AND s.amount_cents=o.amount_cents AND s.counterparty=o.counterparty
    AND (k.source_id IS NOT NULL OR ko.source_id IS NOT NULL)
    AND (k.source_id IS NULL OR ko.source_id IS NULL OR k.bank_account=ko.bank_account)
    AND NOT EXISTS(SELECT 1 FROM accounting.bank_duplicate_decisions d WHERE d.book_id=target_book
     AND d.source_id=least(s.id,o.id) AND d.other_source_id=greatest(s.id,o.id)));
 END $$;

CREATE OR REPLACE FUNCTION accounting.bank_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Bank identity and duplicate reviews are immutable' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='bank_import_keys' THEN
   -- Preserve all original key publication and open-period scope checks.
   IF NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.books b ON b.id=s.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id
    AND s.source_kind='bank' AND s.external_id='bank:v1:'||NEW.source_id AND s.fingerprint=NEW.content_fingerprint
    AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND p.status='open' AND substr(s.date,1,7)=b.period) THEN RAISE EXCEPTION 'Bank identity scope mismatch' USING ERRCODE='23514'; END IF;
  ELSE
   IF NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.sources o ON o.id=NEW.other_source_id AND o.book_id=s.book_id
    LEFT JOIN accounting.bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id
    LEFT JOIN accounting.bank_import_keys ko ON ko.source_id=o.id AND ko.book_id=o.book_id
    JOIN accounting.books b ON b.id=s.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
    WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND b.firm_id=NEW.firm_id AND p.status='open'
     AND s.source_kind='bank' AND o.source_kind='bank'
     AND s.fingerprint=NEW.source_fingerprint AND o.fingerprint=NEW.other_fingerprint
     AND (k.source_id IS NOT NULL OR ko.source_id IS NOT NULL)
     AND (k.source_id IS NULL OR ko.source_id IS NULL OR k.bank_account=ko.bank_account)
     AND s.date=o.date AND s.direction=o.direction AND s.counterparty=o.counterparty AND s.amount_cents=o.amount_cents
     AND EXISTS(SELECT 1 FROM accounting.drafts d WHERE d.source_id IN (s.id,o.id) AND d.book_id=s.book_id AND d.status='draft' AND d.version=NEW.expected_draft_version))
    THEN RAISE EXCEPTION 'Duplicate decision must use current canonical bank facts' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END $$;
REVOKE ALL ON FUNCTION accounting.bank_identity_guard() FROM PUBLIC;
REVOKE ALL ON FUNCTION accounting.bank_source_import_blocked(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.bank_source_import_blocked(TEXT,TEXT) TO accounting_app;
