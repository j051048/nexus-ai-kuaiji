CREATE TRIGGER learning_ordinary_version BEFORE INSERT ON learning_rule_versions WHEN EXISTS(
 SELECT 1 FROM json_each(NEW.sample_ids_json) ref JOIN learning_samples x ON x.id=ref.value AND x.book_id=NEW.book_id JOIN sources s ON s.id=x.source_id
 JOIN settlement_allocations a ON a.bank_source_id=s.id WHERE s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)
) BEGIN SELECT RAISE(ABORT,'Clearance samples cannot become ordinary classification rules');END;
CREATE TRIGGER learning_ordinary_evaluation BEFORE INSERT ON learning_rule_evaluations WHEN EXISTS(
 SELECT 1 FROM (SELECT ref.value AS id FROM json_each(NEW.negative_sample_ids_json) ref UNION ALL SELECT ref.value FROM learning_rule_versions v,json_each(v.sample_ids_json) ref WHERE v.rule_id=NEW.rule_id AND v.book_id=NEW.book_id AND v.version=NEW.rule_version) ids
 JOIN learning_samples x ON x.id=ids.id AND x.book_id=NEW.book_id JOIN sources s ON s.id=x.source_id JOIN settlement_allocations a ON a.bank_source_id=s.id WHERE s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)
) BEGIN SELECT RAISE(ABORT,'Clearance samples cannot be ordinary classification evaluation examples');END;
CREATE TRIGGER learning_ordinary_publish BEFORE UPDATE OF status ON learning_rules WHEN NEW.status='published' AND EXISTS(
 SELECT 1 FROM (SELECT ref.value AS id FROM learning_rule_versions v,json_each(v.sample_ids_json) ref WHERE v.rule_id=NEW.id AND v.book_id=NEW.book_id AND v.version=NEW.current_version UNION ALL SELECT ref.value FROM learning_rule_evaluations e,json_each(e.negative_sample_ids_json) ref WHERE e.id=NEW.evaluation_id AND e.rule_id=NEW.id AND e.book_id=NEW.book_id AND e.rule_version=NEW.current_version) ids
 JOIN learning_samples x ON x.id=ids.id AND x.book_id=NEW.book_id JOIN sources s ON s.id=x.source_id JOIN settlement_allocations a ON a.bank_source_id=s.id WHERE s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)
) BEGIN SELECT RAISE(ABORT,'Clearance samples cannot authorize ordinary classification publication');END;
