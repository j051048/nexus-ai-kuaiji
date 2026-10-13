-- Existing samples remain immutable; historical clearance is never ordinary training.
CREATE TRIGGER learning_legacy_capture BEFORE INSERT ON learning_samples WHEN EXISTS(
 SELECT 1 FROM migration_clearance_allocations a WHERE a.bank_source_id=NEW.source_id AND a.bank_book_id=NEW.book_id AND NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=a.command_id)
) BEGIN SELECT RAISE(ABORT,'Historical clearance cannot be an ordinary classification sample');END;
CREATE TRIGGER learning_legacy_version BEFORE INSERT ON learning_rule_versions WHEN EXISTS(
 SELECT 1 FROM json_each(NEW.sample_ids_json) ref JOIN learning_samples x ON x.id=ref.value AND x.book_id=NEW.book_id
 JOIN migration_clearance_allocations a ON a.bank_source_id=x.source_id AND a.bank_book_id=x.book_id WHERE NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=a.command_id)
) BEGIN SELECT RAISE(ABORT,'Historical clearance cannot authorize ordinary classification versions');END;
CREATE TRIGGER learning_legacy_evaluation BEFORE INSERT ON learning_rule_evaluations WHEN EXISTS(
 SELECT 1 FROM (SELECT ref.value AS id FROM json_each(NEW.negative_sample_ids_json) ref UNION ALL SELECT ref.value FROM learning_rule_versions v,json_each(v.sample_ids_json) ref WHERE v.rule_id=NEW.rule_id AND v.book_id=NEW.book_id AND v.version=NEW.rule_version) ids
 JOIN learning_samples x ON x.id=ids.id AND x.book_id=NEW.book_id JOIN migration_clearance_allocations a ON a.bank_source_id=x.source_id AND a.bank_book_id=x.book_id WHERE NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=a.command_id)
) BEGIN SELECT RAISE(ABORT,'Historical clearance cannot authorize ordinary classification evaluation');END;
CREATE TRIGGER learning_legacy_publish BEFORE UPDATE OF status ON learning_rules WHEN NEW.status='published' AND EXISTS(
 SELECT 1 FROM (SELECT ref.value AS id FROM learning_rule_versions v,json_each(v.sample_ids_json) ref WHERE v.rule_id=NEW.id AND v.book_id=NEW.book_id AND v.version=NEW.current_version UNION ALL SELECT ref.value FROM learning_rule_evaluations e,json_each(e.negative_sample_ids_json) ref WHERE e.id=NEW.evaluation_id AND e.rule_id=NEW.id AND e.book_id=NEW.book_id AND e.rule_version=NEW.current_version) ids
 JOIN learning_samples x ON x.id=ids.id AND x.book_id=NEW.book_id JOIN migration_clearance_allocations a ON a.bank_source_id=x.source_id AND a.bank_book_id=x.book_id WHERE NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=a.command_id)
) BEGIN SELECT RAISE(ABORT,'Historical clearance cannot authorize ordinary classification publication');END;
