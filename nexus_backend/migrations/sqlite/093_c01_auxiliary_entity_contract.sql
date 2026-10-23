-- compatibility: incremental
-- C01: enforce entity identities on new records, preserve historical rows.
CREATE TRIGGER c01_control_aux_insert BEFORE INSERT ON subledger_control_accounts BEGIN

 SELECT CASE WHEN json_valid(NEW.required_aux_json)=0 THEN RAISE(ABORT,'Invalid auxiliary requirements JSON') END;
 SELECT CASE WHEN json_type(NEW.required_aux_json)<>'array' OR EXISTS(
  SELECT 1 FROM json_each(NEW.required_aux_json) WHERE type<>'text'
   OR value NOT IN('department','counterparty','employee','project')) OR
  (SELECT count(*) FROM json_each(NEW.required_aux_json))<>
  (SELECT count(DISTINCT value) FROM json_each(NEW.required_aux_json))
 THEN RAISE(ABORT,'Unsupported or duplicate auxiliary dimensions') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ledger_accounts a
  JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=NEW.control_account_version
  WHERE a.id=NEW.control_account_id AND a.ledger_id=NEW.ledger_id
  AND NOT EXISTS(SELECT 1 FROM json_each(v.auxiliary_requirements) needed
   WHERE NOT EXISTS(SELECT 1 FROM json_each(NEW.required_aux_json) actual WHERE actual.value=needed.value)))
 THEN RAISE(ABORT,'Control configuration omits account auxiliary requirements') END;
END;
CREATE TRIGGER c01_control_aux_update BEFORE UPDATE ON subledger_control_accounts BEGIN

 SELECT CASE WHEN json_valid(NEW.required_aux_json)=0 THEN RAISE(ABORT,'Invalid auxiliary requirements JSON') END;
 SELECT CASE WHEN json_type(NEW.required_aux_json)<>'array' OR EXISTS(
  SELECT 1 FROM json_each(NEW.required_aux_json) WHERE type<>'text'
   OR value NOT IN('department','counterparty','employee','project')) OR
  (SELECT count(*) FROM json_each(NEW.required_aux_json))<>
  (SELECT count(DISTINCT value) FROM json_each(NEW.required_aux_json))
 THEN RAISE(ABORT,'Unsupported or duplicate auxiliary dimensions') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ledger_accounts a
  JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=NEW.control_account_version
  WHERE a.id=NEW.control_account_id AND a.ledger_id=NEW.ledger_id
  AND NOT EXISTS(SELECT 1 FROM json_each(v.auxiliary_requirements) needed
   WHERE NOT EXISTS(SELECT 1 FROM json_each(NEW.required_aux_json) actual WHERE actual.value=needed.value)))
 THEN RAISE(ABORT,'Control configuration omits account auxiliary requirements') END;
END;
CREATE TRIGGER c01_item_aux_insert BEFORE INSERT ON subledger_items BEGIN

 SELECT CASE WHEN json_valid(NEW.aux_json)=0 THEN RAISE(ABORT,'Invalid auxiliary JSON') END;
 SELECT CASE WHEN json_type(NEW.aux_json)<>'object' THEN RAISE(ABORT,'Auxiliary values must be an object') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ledger_periods p WHERE p.book_id=NEW.book_id
  AND p.ledger_id=NEW.ledger_id AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id
  AND p.period=NEW.period) THEN RAISE(ABORT,'Subledger book ledger scope mismatch') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM subledger_control_accounts c
  WHERE c.ledger_id=NEW.ledger_id AND c.firm_id=NEW.firm_id AND c.company_id=NEW.company_id
   AND c.kind=NEW.kind AND c.control_account_id=NEW.control_account_id
   AND c.control_account_version=NEW.control_account_version AND c.active=1
   AND c.effective_from<=substr(NEW.original_date,1,7)
   AND (c.effective_to IS NULL OR c.effective_to>=substr(NEW.original_date,1,7))
   AND NOT EXISTS(SELECT 1 FROM json_each(c.required_aux_json) r
    WHERE NOT EXISTS(SELECT 1 FROM json_each(NEW.aux_json) a WHERE a.key=r.value)))
 THEN RAISE(ABORT,'Subledger control configuration or required auxiliary missing') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM company_master_entities e WHERE e.id=NEW.counterparty_id
  AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.kind='counterparty' AND e.active=1)
 THEN RAISE(ABORT,'Subledger counterparty invalid') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.aux_json) a WHERE a.type<>'text'
  OR length(a.value) NOT BETWEEN 1 AND 100 OR a.value<>trim(a.value)
  OR CASE a.key
   WHEN 'counterparty' THEN a.value<>NEW.counterparty_id
   WHEN 'project' THEN NOT EXISTS(SELECT 1 FROM company_master_entities e WHERE e.id=a.value
    AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.kind='project' AND e.active=1)
   WHEN 'department' THEN NOT EXISTS(SELECT 1 FROM departments d
    WHERE d.id=a.value AND d.company_id=NEW.company_id)
   WHEN 'employee' THEN NOT EXISTS(SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id
    WHERE cm.user_id=a.value AND cm.company_id=NEW.company_id AND u.active=1)
   ELSE 1 END)
 THEN RAISE(ABORT,'Subledger auxiliary entity or ownership mismatch') END;
END;
