-- compatibility: incremental
-- Protect independently reviewed references; do not rewrite historical evidence.
CREATE TRIGGER c01_bank_resolved_update BEFORE UPDATE ON bank_statement_lines BEGIN SELECT CASE WHEN EXISTS(SELECT 1 FROM bank_adjustment_resolutions WHERE bank_line_id=OLD.id) THEN RAISE(ABORT,'Resolved bank evidence is immutable') END; END;
CREATE TRIGGER c01_request_update BEFORE UPDATE ON subledger_allocation_requests BEGIN SELECT RAISE(ABORT,'Allocation request immutable'); END;
CREATE TRIGGER c01_bank_resolved_delete BEFORE DELETE ON bank_statement_lines BEGIN SELECT CASE WHEN EXISTS(SELECT 1 FROM bank_adjustment_resolutions WHERE bank_line_id=OLD.id) THEN RAISE(ABORT,'Resolved bank evidence is immutable') END; END;
CREATE TRIGGER c01_request_delete BEFORE DELETE ON subledger_allocation_requests BEGIN SELECT RAISE(ABORT,'Allocation request immutable'); END;
CREATE TRIGGER c01_review_bank_statement_lines_insert BEFORE INSERT ON bank_statement_lines BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) THEN RAISE(ABORT,'Current C01 reviewer required') END; END;
CREATE TRIGGER c01_review_bank_statement_lines_update BEFORE UPDATE ON bank_statement_lines BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) THEN RAISE(ABORT,'Current C01 reviewer required') END; END;
CREATE TRIGGER c01_review_bank_statement_lines_delete BEFORE DELETE ON bank_statement_lines BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=OLD.book_id AND user_id=accounting_actor_user() AND can_review) THEN RAISE(ABORT,'Current C01 reviewer required') END; END;
CREATE TRIGGER c01_review_bank_accounts_insert BEFORE INSERT ON bank_accounts BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) THEN RAISE(ABORT,'Current C01 reviewer required') END; END;
CREATE TRIGGER c01_review_bank_accounts_update BEFORE UPDATE ON bank_accounts BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) THEN RAISE(ABORT,'Current C01 reviewer required') END; END;
CREATE TRIGGER c01_review_bank_accounts_delete BEFORE DELETE ON bank_accounts BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=OLD.book_id AND user_id=accounting_actor_user() AND can_review) THEN RAISE(ABORT,'Current C01 reviewer required') END; END;
CREATE TRIGGER c01_review_subledger_allocation_requests_insert BEFORE INSERT ON subledger_allocation_requests BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) THEN RAISE(ABORT,'Current C01 reviewer required') END; END;
CREATE TRIGGER c01_bank_line_author BEFORE INSERT ON bank_statement_lines BEGIN SELECT CASE WHEN NEW.created_by IS NOT accounting_actor_user() THEN RAISE(ABORT,'Current bank statement author required') END; END;
CREATE TRIGGER c01_bank_identity_frozen BEFORE UPDATE ON bank_accounts BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM c01_approved_banks WHERE bank_account_id=OLD.id)
 AND (NEW.book_id IS NOT OLD.book_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.ledger_account_id IS NOT OLD.ledger_account_id OR NEW.ledger_account_version IS NOT OLD.ledger_account_version
 OR EXISTS(SELECT key,value FROM json_each(NEW.aux_json) EXCEPT SELECT key,value FROM json_each(OLD.aux_json))
 OR EXISTS(SELECT key,value FROM json_each(OLD.aux_json) EXCEPT SELECT key,value FROM json_each(NEW.aux_json)))
 THEN RAISE(ABORT,'Reviewed bank identity mapping is immutable') END; END;
CREATE TRIGGER c01_request_scope BEFORE INSERT ON subledger_allocation_requests BEGIN
 SELECT CASE WHEN NEW.created_by IS NOT accounting_actor_user() OR NEW.firm_id IS NOT accounting_actor_firm()
 OR NOT EXISTS(SELECT 1 FROM books b JOIN vouchers v ON v.book_id=b.id WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND v.id=NEW.voucher_id)
 THEN RAISE(ABORT,'Allocation request scope mismatch') END; END;
CREATE TRIGGER c01_bank_aux_insert BEFORE INSERT ON bank_accounts BEGIN
 SELECT CASE WHEN json_type(NEW.aux_json)<>'object' OR NOT EXISTS(SELECT 1 FROM ledger_accounts la JOIN ledger_account_versions av ON av.account_id=la.id AND av.version=NEW.ledger_account_version
 WHERE la.id=NEW.ledger_account_id AND av.is_leaf AND av.category='asset' AND NOT EXISTS(SELECT 1 FROM json_each(av.auxiliary_requirements) needed WHERE NOT EXISTS(SELECT 1 FROM json_each(NEW.aux_json) actual WHERE actual.key=needed.value)))
 THEN RAISE(ABORT,'Bank requires leaf asset auxiliary mapping') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.aux_json) a WHERE a.type<>'text' OR CASE a.key
 WHEN 'counterparty' THEN NOT EXISTS(SELECT 1 FROM company_master_entities e WHERE e.id=a.value AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.kind='counterparty' AND e.active=1)
 WHEN 'project' THEN NOT EXISTS(SELECT 1 FROM company_master_entities e WHERE e.id=a.value AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.kind='project' AND e.active=1)
 WHEN 'department' THEN NOT EXISTS(SELECT 1 FROM departments WHERE id=a.value AND company_id=NEW.company_id)
 WHEN 'employee' THEN NOT EXISTS(SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id WHERE cm.user_id=a.value AND cm.company_id=NEW.company_id AND u.active=1)
 ELSE 1 END) THEN RAISE(ABORT,'Bank auxiliary entity invalid') END; END;
CREATE TRIGGER c01_bank_aux_update BEFORE UPDATE ON bank_accounts BEGIN
 SELECT CASE WHEN json_type(NEW.aux_json)<>'object' OR NOT EXISTS(SELECT 1 FROM ledger_accounts la JOIN ledger_account_versions av ON av.account_id=la.id AND av.version=NEW.ledger_account_version
 WHERE la.id=NEW.ledger_account_id AND av.is_leaf AND av.category='asset' AND NOT EXISTS(SELECT 1 FROM json_each(av.auxiliary_requirements) needed WHERE NOT EXISTS(SELECT 1 FROM json_each(NEW.aux_json) actual WHERE actual.key=needed.value)))
 THEN RAISE(ABORT,'Bank requires leaf asset auxiliary mapping') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.aux_json) a WHERE a.type<>'text' OR CASE a.key
 WHEN 'counterparty' THEN NOT EXISTS(SELECT 1 FROM company_master_entities e WHERE e.id=a.value AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.kind='counterparty' AND e.active=1)
 WHEN 'project' THEN NOT EXISTS(SELECT 1 FROM company_master_entities e WHERE e.id=a.value AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.kind='project' AND e.active=1)
 WHEN 'department' THEN NOT EXISTS(SELECT 1 FROM departments WHERE id=a.value AND company_id=NEW.company_id)
 WHEN 'employee' THEN NOT EXISTS(SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id WHERE cm.user_id=a.value AND cm.company_id=NEW.company_id AND u.active=1)
 ELSE 1 END) THEN RAISE(ABORT,'Bank auxiliary entity invalid') END; END;
