ALTER TABLE proposal_jobs ADD COLUMN result_rule_id TEXT;
ALTER TABLE proposal_jobs ADD COLUMN result_rule_version INTEGER;
CREATE TRIGGER proposal_rule_receipt_guard_insert BEFORE INSERT ON proposal_jobs
 WHEN (NEW.result_rule_id IS NULL)<>(NEW.result_rule_version IS NULL)
 OR (NEW.result_rule_id IS NOT NULL AND (NEW.result_template_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM learning_rule_versions v JOIN learning_rules r ON r.id=v.rule_id AND r.book_id=v.book_id WHERE v.rule_id=NEW.result_rule_id AND v.version=NEW.result_rule_version AND v.book_id=NEW.book_id AND r.firm_id=NEW.firm_id AND r.company_id=NEW.company_id)))
 BEGIN SELECT RAISE(ABORT,'Company rule receipt must reference its scoped immutable version'); END;
CREATE TRIGGER proposal_rule_receipt_guard_update BEFORE UPDATE ON proposal_jobs
 WHEN (NEW.result_rule_id IS NULL)<>(NEW.result_rule_version IS NULL)
 OR (NEW.result_rule_id IS NOT NULL AND (NEW.result_template_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM learning_rule_versions v JOIN learning_rules r ON r.id=v.rule_id AND r.book_id=v.book_id WHERE v.rule_id=NEW.result_rule_id AND v.version=NEW.result_rule_version AND v.book_id=NEW.book_id AND r.firm_id=NEW.firm_id AND r.company_id=NEW.company_id)))
 BEGIN SELECT RAISE(ABORT,'Company rule receipt must reference its scoped immutable version'); END;
CREATE INDEX idx_case_blocking_source ON case_tasks(firm_id,source_id,state);
CREATE INDEX idx_case_blocking_event ON case_tasks(firm_id,event_id,state);
