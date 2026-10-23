-- compatibility: incremental
-- C01: validate only new control/item writes. Immutable history is not rewritten.
CREATE FUNCTION accounting.c01_aux_requirements_valid(raw_value TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE values_json JSONB;
BEGIN
 values_json:=raw_value::jsonb;
 IF jsonb_typeof(values_json) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 RETURN NOT EXISTS(SELECT 1 FROM jsonb_array_elements(values_json) v
  WHERE jsonb_typeof(v) IS DISTINCT FROM 'string'
  OR v#>>'{}' NOT IN ('department','counterparty','employee','project'))
  AND (SELECT count(*) FROM jsonb_array_elements(values_json))=
      (SELECT count(DISTINCT v) FROM jsonb_array_elements(values_json) v);
END $$;
REVOKE ALL ON FUNCTION accounting.c01_aux_requirements_valid(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.c01_aux_requirements_valid(TEXT) TO accounting_app;

CREATE FUNCTION accounting.c01_subledger_aux_valid(target TEXT,dimension TEXT,entity TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target) AND length(entity) BETWEEN 1 AND 100
 AND entity=btrim(entity) AND EXISTS(SELECT 1 FROM accounting.books b
 WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND CASE dimension
  WHEN 'counterparty' THEN EXISTS(SELECT 1 FROM accounting.company_master_entities e
   WHERE e.id=entity AND e.firm_id=b.firm_id AND e.company_id=b.company_id
    AND e.kind='counterparty' AND e.active=1)
  WHEN 'project' THEN EXISTS(SELECT 1 FROM accounting.company_master_entities e
   WHERE e.id=entity AND e.firm_id=b.firm_id AND e.company_id=b.company_id
    AND e.kind='project' AND e.active=1)
  WHEN 'department' THEN EXISTS(SELECT 1 FROM accounting.departments d
   WHERE d.id=entity AND d.company_id=b.company_id)
  WHEN 'employee' THEN EXISTS(SELECT 1 FROM accounting.company_memberships cm
   JOIN accounting.users u ON u.id=cm.user_id WHERE cm.company_id=b.company_id
    AND cm.user_id=entity AND u.active=1)
  ELSE false END)
$$;
REVOKE ALL ON FUNCTION accounting.c01_subledger_aux_valid(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.c01_subledger_aux_valid(TEXT,TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.c01_control_aux_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE account_aux TEXT;
BEGIN
 IF NOT accounting.c01_aux_requirements_valid(NEW.required_aux_json) THEN
  RAISE EXCEPTION 'Unsupported or duplicate control auxiliary dimensions' USING ERRCODE='23514';
 END IF;
 SELECT v.auxiliary_requirements INTO account_aux FROM accounting.ledger_accounts a
 JOIN accounting.ledger_account_versions v ON v.account_id=a.id AND v.version=NEW.control_account_version
 WHERE a.id=NEW.control_account_id AND a.ledger_id=NEW.ledger_id;
 IF account_aux IS NULL OR NOT accounting.c01_aux_requirements_valid(account_aux)
  OR NOT NEW.required_aux_json::jsonb @> account_aux::jsonb THEN
  RAISE EXCEPTION 'Control configuration omits account auxiliary requirements' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER c01_control_aux_guard BEFORE INSERT OR UPDATE
 ON accounting.subledger_control_accounts FOR EACH ROW
 EXECUTE FUNCTION accounting.c01_control_aux_guard();
REVOKE ALL ON FUNCTION accounting.c01_control_aux_guard() FROM PUBLIC;

CREATE FUNCTION accounting.c01_item_aux_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE config RECORD; dimensions JSONB; auxiliary RECORD;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.ledger_periods p
  WHERE p.book_id=NEW.book_id AND p.ledger_id=NEW.ledger_id AND p.firm_id=NEW.firm_id
   AND p.company_id=NEW.company_id AND p.period=NEW.period) THEN
  RAISE EXCEPTION 'Subledger scope does not match book and ledger' USING ERRCODE='23514';
 END IF;
 SELECT c.* INTO config FROM accounting.subledger_control_accounts c
 WHERE c.ledger_id=NEW.ledger_id AND c.firm_id=NEW.firm_id AND c.company_id=NEW.company_id
  AND c.kind=NEW.kind AND c.control_account_id=NEW.control_account_id
  AND c.control_account_version=NEW.control_account_version AND c.active
  AND c.effective_from<=left(NEW.original_date,7)
  AND (c.effective_to IS NULL OR c.effective_to>=left(NEW.original_date,7));
 IF NOT FOUND THEN RAISE EXCEPTION 'Subledger control configuration missing' USING ERRCODE='23514'; END IF;
 IF NOT accounting.c01_aux_requirements_valid(config.required_aux_json) THEN
  RAISE EXCEPTION 'Subledger control dimensions are invalid' USING ERRCODE='23514'; END IF;
 dimensions:=NEW.aux_json::jsonb;
 IF jsonb_typeof(dimensions) IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'Subledger auxiliary values must be an object' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(config.required_aux_json::jsonb) r(key)
  WHERE NOT dimensions ? r.key) THEN
  RAISE EXCEPTION 'Subledger required auxiliary value missing' USING ERRCODE='23514'; END IF;
 IF NOT accounting.c01_subledger_aux_valid(NEW.book_id,'counterparty',NEW.counterparty_id) THEN
  RAISE EXCEPTION 'Subledger counterparty entity is not valid in company' USING ERRCODE='23514'; END IF;
 FOR auxiliary IN SELECT * FROM jsonb_each(dimensions) LOOP
  IF jsonb_typeof(auxiliary.value) IS DISTINCT FROM 'string'
   OR NOT accounting.c01_subledger_aux_valid(NEW.book_id,auxiliary.key,auxiliary.value#>>'{}')
   OR (auxiliary.key='counterparty' AND auxiliary.value#>>'{}' IS DISTINCT FROM NEW.counterparty_id)
  THEN RAISE EXCEPTION 'Subledger auxiliary entity or ownership mismatch' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER c01_item_aux_guard BEFORE INSERT ON accounting.subledger_items
 FOR EACH ROW EXECUTE FUNCTION accounting.c01_item_aux_guard();
REVOKE ALL ON FUNCTION accounting.c01_item_aux_guard() FROM PUBLIC;
