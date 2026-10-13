-- SQLite is a local demonstration backend; retain raw names and guard identity scope.
CREATE VIEW learning_identity_names AS
 SELECT e.firm_id,e.company_id,e.id AS entity_id,e.name AS original FROM company_master_entities e WHERE e.kind='counterparty' AND e.active=1
 UNION
 SELECT e.firm_id,e.company_id,e.id,a.original FROM company_master_entities e JOIN company_master_aliases a ON a.entity_id=e.id AND a.firm_id=e.firm_id AND a.company_id=e.company_id
 WHERE e.kind='counterparty' AND e.active=1 AND a.kind='counterparty' AND a.status='approved' AND a.reviewed_by IS NOT NULL AND a.reviewed_by<>a.created_by;
CREATE TRIGGER learning_identity_version_guard BEFORE INSERT ON learning_rule_versions
 WHEN json_extract(NEW.definition_json,'$.conditions.counterparty_id') IS NOT NULL BEGIN
 SELECT CASE WHEN json_type(NEW.definition_json,'$.conditions.counterparty_id')<>'text'
 OR NOT EXISTS(SELECT 1 FROM books b JOIN learning_identity_names n ON n.firm_id=b.firm_id AND n.company_id=b.company_id WHERE b.id=NEW.book_id
  AND n.entity_id=json_extract(NEW.definition_json,'$.conditions.counterparty_id') AND n.original=json_extract(NEW.definition_json,'$.conditions.counterparty')
  AND (SELECT count(DISTINCT x.entity_id) FROM learning_identity_names x WHERE x.firm_id=n.firm_id AND x.company_id=n.company_id AND x.original=n.original)=1)
 THEN RAISE(ABORT,'Learning identity must resolve uniquely to own active company master') END; END;
CREATE TRIGGER learning_semantic_duplicate_guard BEFORE INSERT ON learning_rule_versions BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version
 WHERE r.book_id=NEW.book_id AND r.id<>NEW.rule_id AND r.status<>'retired'
 AND json_extract(v.definition_json,'$.source_kind') IS json_extract(NEW.definition_json,'$.source_kind')
 AND json_extract(v.definition_json,'$.conditions.business_type') IS json_extract(NEW.definition_json,'$.conditions.business_type')
 AND json_extract(v.definition_json,'$.conditions.direction') IS json_extract(NEW.definition_json,'$.conditions.direction')
 AND json_extract(v.definition_json,'$.conditions.category') IS json_extract(NEW.definition_json,'$.conditions.category')
 AND json_extract(v.definition_json,'$.conditions.payment_status') IS json_extract(NEW.definition_json,'$.conditions.payment_status')
 AND json_extract(v.definition_json,'$.conditions.counterparty_id') IS json_extract(NEW.definition_json,'$.conditions.counterparty_id')
 AND (json_extract(NEW.definition_json,'$.conditions.counterparty_id') IS NOT NULL OR json_extract(v.definition_json,'$.conditions.counterparty') IS json_extract(NEW.definition_json,'$.conditions.counterparty'))
 AND (SELECT json_group_array(json(item)) FROM (SELECT json_array(json_extract(value,'$.account_code'),json_extract(value,'$.side'),json_extract(value,'$.amount_field')) AS item FROM json_each(v.definition_json,'$.lines') ORDER BY item))
  =(SELECT json_group_array(json(item)) FROM (SELECT json_array(json_extract(value,'$.account_code'),json_extract(value,'$.side'),json_extract(value,'$.amount_field')) AS item FROM json_each(NEW.definition_json,'$.lines') ORDER BY item)))
 THEN RAISE(ABORT,'Duplicate learning semantics require explicit revision; samples are not merged') END; END;
CREATE TRIGGER learning_identity_publish_guard BEFORE UPDATE OF status ON learning_rules WHEN NEW.status='published' BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM learning_rule_versions v JOIN books b ON b.id=v.book_id
  WHERE v.rule_id=NEW.id AND v.version=NEW.current_version AND json_extract(v.definition_json,'$.conditions.counterparty_id') IS NOT NULL
  AND NOT EXISTS(SELECT 1 FROM learning_identity_names n WHERE n.firm_id=b.firm_id AND n.company_id=b.company_id AND n.original=json_extract(v.definition_json,'$.conditions.counterparty')
   AND n.entity_id=json_extract(v.definition_json,'$.conditions.counterparty_id') AND (SELECT count(DISTINCT x.entity_id) FROM learning_identity_names x WHERE x.firm_id=n.firm_id AND x.company_id=n.company_id AND x.original=n.original)=1))
 THEN RAISE(ABORT,'Learning publication identity requires review') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM learning_rules r JOIN learning_rule_versions other ON other.rule_id=r.id AND other.book_id=r.book_id AND other.version=r.current_version
  JOIN learning_rule_versions current ON current.rule_id=NEW.id AND current.book_id=NEW.book_id AND current.version=NEW.current_version JOIN books b ON b.id=NEW.book_id
 WHERE r.book_id=NEW.book_id AND r.id<>NEW.id AND r.status='published'
 AND json_extract(other.definition_json,'$.source_kind') IS json_extract(current.definition_json,'$.source_kind')
 AND json_extract(other.definition_json,'$.conditions.business_type') IS json_extract(current.definition_json,'$.conditions.business_type')
 AND json_extract(other.definition_json,'$.conditions.direction') IS json_extract(current.definition_json,'$.conditions.direction')
 AND json_extract(other.definition_json,'$.conditions.payment_status') IS json_extract(current.definition_json,'$.conditions.payment_status')
 AND (json_extract(other.definition_json,'$.conditions.category') IS NULL OR json_extract(current.definition_json,'$.conditions.category') IS NULL OR json_extract(other.definition_json,'$.conditions.category') IS json_extract(current.definition_json,'$.conditions.category'))
 AND CASE WHEN json_extract(other.definition_json,'$.conditions.counterparty_id') IS NULL AND json_extract(current.definition_json,'$.conditions.counterparty_id') IS NULL
 THEN json_extract(other.definition_json,'$.conditions.counterparty') IS json_extract(current.definition_json,'$.conditions.counterparty')
 ELSE coalesce(json_extract(other.definition_json,'$.conditions.counterparty_id'),(SELECT min(n.entity_id) FROM learning_identity_names n WHERE n.firm_id=b.firm_id AND n.company_id=b.company_id AND n.original=json_extract(other.definition_json,'$.conditions.counterparty') HAVING count(DISTINCT n.entity_id)=1))
  =coalesce(json_extract(current.definition_json,'$.conditions.counterparty_id'),(SELECT min(n.entity_id) FROM learning_identity_names n WHERE n.firm_id=b.firm_id AND n.company_id=b.company_id AND n.original=json_extract(current.definition_json,'$.conditions.counterparty') HAVING count(DISTINCT n.entity_id)=1)) END)
 THEN RAISE(ABORT,'Learning publication overlapping rule conflict requires review') END; END;
