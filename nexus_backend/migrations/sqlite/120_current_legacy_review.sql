-- compatibility: breaking
-- Current human rechecks are separate from immutable historical approvals.
CREATE TABLE legacy_review_preparations(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN('draft','manual','periodic')),
 subject_id TEXT NOT NULL, book_id TEXT NOT NULL REFERENCES books(id),
 source_version INTEGER NOT NULL, reviewed_version INTEGER NOT NULL,
 content_revision INTEGER NOT NULL, content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),
 policy_version INTEGER NOT NULL, mode TEXT NOT NULL CHECK(mode IN('independent','single_operator')),
 maker_user_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 created_at TEXT NOT NULL, UNIQUE(kind,subject_id,source_version,policy_version));
CREATE TABLE legacy_review_confirmations(
 request_id TEXT PRIMARY KEY REFERENCES legacy_review_preparations(id),book_id TEXT NOT NULL REFERENCES books(id),
 reviewer_user_id TEXT NOT NULL REFERENCES users(id),role_snapshot TEXT NOT NULL CHECK(role_snapshot IN('owner','admin','reviewer')),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),reviewed_at TEXT NOT NULL);

CREATE VIEW legacy_review_sources AS
 SELECT 'draft' AS kind,d.id AS subject_id,d.book_id,d.version AS source_version,
 d.version-CASE WHEN d.status='exported' THEN 2 ELSE 1 END AS reviewed_version,
 COALESCE(p.content_revision,1) AS content_revision,c.fingerprint AS content_fingerprint,
 p.maker_user_id AS known_maker,d.approved_by AS original_reviewer
 FROM drafts d JOIN rr_draft_contents c ON c.draft_id=d.id LEFT JOIN draft_preparations p ON p.draft_id=d.id
 WHERE d.status IN('approved','exported')
 UNION ALL SELECT 'manual',d.id,d.book_id,d.version,d.version,1,d.content_fingerprint,d.prepared_by,d.reviewed_by
 FROM manual_review_requests d WHERE d.status='approved'
 UNION ALL SELECT 'periodic',d.id,d.book_id,d.version,d.version,1,accounting_sha256(json_array(d.book_id,d.description,d.version,(SELECT json_group_array(json(x)) FROM (SELECT json_array(l.line_no,l.account_id,l.account_version,l.debit_cents,l.credit_cents,l.aux_json,l.source_ref) x FROM periodic_draft_lines l WHERE l.draft_id=d.id ORDER BY l.line_no)))),d.created_by,d.approved_by
 FROM periodic_drafts d WHERE d.status='approved';
CREATE VIEW firm_review_evidence AS
 SELECT 'draft' AS kind,r.draft_id AS subject_id,r.book_id,r.reviewed_version,r.content_revision,r.content_fingerprint,r.maker_user_id,r.reviewer_user_id,r.policy_version,r.mode,r.independent,r.reason,r.reviewed_at,'review_time' AS proof_kind
 FROM draft_review_receipts r JOIN review_authority_snapshots a ON a.subject_kind='draft' AND a.subject_id=r.draft_id AND a.book_id=r.book_id AND a.reviewed_version=r.reviewed_version AND a.proof_kind='review_time'
 UNION ALL SELECT 'manual',r.id,r.book_id,r.version,1,r.content_fingerprint,r.prepared_by,r.reviewed_by,r.policy_version,COALESCE(p.mode,'independent'),r.independent,r.review_reason,r.reviewed_at,'review_time'
 FROM manual_review_requests r JOIN review_authority_snapshots a ON a.subject_kind='manual' AND a.subject_id=r.id AND a.book_id=r.book_id AND a.reviewed_version=r.version AND a.proof_kind='review_time' LEFT JOIN ledger_periods lp ON lp.book_id=r.book_id LEFT JOIN ledger_review_policies p ON p.ledger_id=lp.ledger_id AND p.version=r.policy_version
 UNION ALL SELECT 'periodic',r.id,r.book_id,r.version,1,s.content_fingerprint,r.created_by,r.approved_by,0,'independent',1,'期末独立复核',r.approved_at,'review_time'
 FROM periodic_drafts r JOIN legacy_review_sources s ON s.kind='periodic' AND s.subject_id=r.id JOIN review_authority_snapshots a ON a.subject_kind='periodic' AND a.subject_id=r.id AND a.book_id=r.book_id AND a.reviewed_version=r.version AND a.proof_kind='review_time'
 UNION ALL SELECT p.kind,p.subject_id,p.book_id,p.reviewed_version,p.content_revision,p.content_fingerprint,p.maker_user_id,c.reviewer_user_id,p.policy_version,p.mode,CASE WHEN p.mode='independent' THEN 1 ELSE 0 END,c.reason,c.reviewed_at,'current_recheck'
 FROM legacy_review_preparations p JOIN legacy_review_confirmations c ON c.request_id=p.id AND c.book_id=p.book_id;
CREATE VIEW effective_draft_review_receipts AS SELECT subject_id AS draft_id,reviewed_version,book_id,maker_user_id,reviewer_user_id,policy_version,mode,independent,reason,reviewed_at,content_revision,content_fingerprint FROM firm_review_evidence WHERE kind='draft';
CREATE TRIGGER current_review_guard_legacy_review_preparations BEFORE INSERT ON legacy_review_preparations BEGIN SELECT CASE WHEN NOT COALESCE((EXISTS(SELECT 1 FROM books b JOIN service_book_user_permissions bp ON bp.book_id=b.id WHERE b.id=NEW.book_id AND b.firm_id=accounting_actor_firm() AND bp.user_id=accounting_actor_user() AND bp.can_edit AND NEW.maker_user_id=accounting_actor_user()) AND EXISTS(SELECT 1 FROM legacy_review_sources s JOIN rr_book_policies pol ON pol.book_id=s.book_id
 JOIN books b ON b.id=s.book_id JOIN periods pr ON pr.book_id=b.id AND pr.period=b.period
 WHERE s.kind=NEW.kind AND s.subject_id=NEW.subject_id AND s.book_id=NEW.book_id
 AND s.source_version=NEW.source_version AND s.reviewed_version=NEW.reviewed_version AND s.content_revision=NEW.content_revision
 AND s.content_fingerprint=NEW.content_fingerprint AND pol.version=NEW.policy_version
 AND NEW.mode=CASE WHEN NEW.kind='periodic' THEN 'independent' ELSE pol.mode END
 AND pr.status='open' AND NOT EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM review_authority_snapshots a WHERE a.subject_kind=s.kind AND a.subject_id=s.subject_id AND a.book_id=s.book_id AND a.reviewed_version=s.reviewed_version AND a.proof_kind='review_time')
 AND NOT EXISTS(SELECT 1 FROM vouchers v WHERE v.book_id=s.book_id AND ((s.kind='draft' AND v.source_kind='draft' AND v.source_id=s.subject_id) OR (s.kind='manual' AND v.review_request_id=s.subject_id) OR (s.kind='periodic' AND v.periodic_review_id=s.subject_id)))) AND NEW.created_at=CURRENT_TIMESTAMP),0) THEN RAISE(ABORT,'Current human authority, exact source and open period required') END; END;
CREATE TRIGGER current_review_no_update_legacy_review_preparations BEFORE UPDATE ON legacy_review_preparations BEGIN SELECT RAISE(ABORT,'Current review immutable'); END;
CREATE TRIGGER current_review_no_delete_legacy_review_preparations BEFORE DELETE ON legacy_review_preparations BEGIN SELECT RAISE(ABORT,'Current review immutable'); END;
CREATE TRIGGER current_review_revision_legacy_review_preparations AFTER INSERT ON legacy_review_preparations BEGIN INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER current_review_guard_legacy_review_confirmations BEFORE INSERT ON legacy_review_confirmations BEGIN SELECT CASE WHEN NOT COALESCE((EXISTS(SELECT 1 FROM legacy_review_preparations p JOIN legacy_review_sources s ON s.kind=p.kind AND s.subject_id=p.subject_id AND s.book_id=p.book_id
 JOIN service_book_user_permissions bp ON bp.book_id=p.book_id JOIN books b ON b.id=p.book_id
 JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=accounting_actor_user()
 WHERE p.id=NEW.request_id AND NEW.book_id=p.book_id AND bp.user_id=accounting_actor_user() AND bp.can_review AND NEW.reviewer_user_id=accounting_actor_user()
 AND NEW.role_snapshot=m.role AND m.role IN('owner','admin','reviewer') AND b.firm_id=accounting_actor_firm()
 AND (p.mode='single_operator' OR (p.maker_user_id<>NEW.reviewer_user_id AND (s.known_maker IS NULL OR s.known_maker<>NEW.reviewer_user_id)))
 AND NOT EXISTS(SELECT 1 FROM business_mappings bm JOIN business_events be ON be.id=bm.event_id WHERE p.kind='draft' AND bm.draft_id=p.subject_id AND be.applicant_id=NEW.reviewer_user_id)
 AND EXISTS(SELECT 1 FROM legacy_review_sources s JOIN rr_book_policies pol ON pol.book_id=s.book_id
 JOIN books b ON b.id=s.book_id JOIN periods pr ON pr.book_id=b.id AND pr.period=b.period
 WHERE s.kind=p.kind AND s.subject_id=p.subject_id AND s.book_id=p.book_id
 AND s.source_version=p.source_version AND s.reviewed_version=p.reviewed_version AND s.content_revision=p.content_revision
 AND s.content_fingerprint=p.content_fingerprint AND pol.version=p.policy_version
 AND p.mode=CASE WHEN p.kind='periodic' THEN 'independent' ELSE pol.mode END
 AND pr.status='open' AND NOT EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM review_authority_snapshots a WHERE a.subject_kind=s.kind AND a.subject_id=s.subject_id AND a.book_id=s.book_id AND a.reviewed_version=s.reviewed_version AND a.proof_kind='review_time')
 AND NOT EXISTS(SELECT 1 FROM vouchers v WHERE v.book_id=s.book_id AND ((s.kind='draft' AND v.source_kind='draft' AND v.source_id=s.subject_id) OR (s.kind='manual' AND v.review_request_id=s.subject_id) OR (s.kind='periodic' AND v.periodic_review_id=s.subject_id))))) AND NEW.reviewed_at=CURRENT_TIMESTAMP),0) THEN RAISE(ABORT,'Current human authority, exact source and open period required') END; END;
CREATE TRIGGER current_review_no_update_legacy_review_confirmations BEFORE UPDATE ON legacy_review_confirmations BEGIN SELECT RAISE(ABORT,'Current review immutable'); END;
CREATE TRIGGER current_review_no_delete_legacy_review_confirmations BEFORE DELETE ON legacy_review_confirmations BEGIN SELECT RAISE(ABORT,'Current review immutable'); END;
CREATE TRIGGER current_review_revision_legacy_review_confirmations AFTER INSERT ON legacy_review_confirmations BEGIN INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1; END;
CREATE VIEW rr_original_verified_voucher_headers AS
 SELECT v.id FROM vouchers v JOIN drafts d ON d.id=v.source_id AND d.book_id=v.book_id JOIN draft_review_receipts r ON r.draft_id=d.id AND r.reviewed_version=CASE WHEN d.status='approved' THEN d.version-1 ELSE d.version-2 END JOIN rr_draft_contents dc ON dc.draft_id=d.id AND dc.fingerprint=r.content_fingerprint
 WHERE v.source_kind='draft' AND v.review_request_id IS NULL AND v.periodic_review_id IS NULL AND d.status IN('approved','exported') AND v.source_version=d.version AND v.relation_kind IS NULL AND v.reversal_of IS NULL AND v.description=d.description AND v.review_mode=r.mode AND v.review_maker_id=r.maker_user_id AND v.review_reviewer_id=r.reviewer_user_id AND v.voucher_review_policy_version=r.policy_version
 UNION ALL SELECT v.id FROM vouchers v JOIN manual_review_requests r ON r.id=v.review_request_id AND r.book_id=v.book_id JOIN ledger_periods lp ON lp.book_id=r.book_id LEFT JOIN ledger_review_policies p ON p.ledger_id=lp.ledger_id AND p.version=r.policy_version
 WHERE v.source_kind IN('manual','adjustment','reversal') AND v.periodic_review_id IS NULL AND v.source_version IS NULL AND v.source_id IS (CASE WHEN v.source_kind='reversal' THEN v.reversal_of ELSE NULL END) AND r.status='approved' AND v.review_mode=COALESCE(p.mode,'independent') AND (r.policy_version=0 OR p.version IS NOT NULL) AND v.review_maker_id=r.prepared_by AND v.review_reviewer_id=r.reviewed_by AND v.voucher_review_policy_version=r.policy_version
 AND v.voucher_date=json_extract(r.content_json,'$.voucher_date') AND v.voucher_type=json_extract(r.content_json,'$.voucher_type') AND v.description=json_extract(r.content_json,'$.description') AND v.relation_kind IS json_extract(r.content_json,'$.relation_kind') AND v.reversal_of IS json_extract(r.content_json,'$.original_voucher_id')
 AND v.source_kind=COALESCE(json_extract(r.content_json,'$.relation_kind'),'manual')
 UNION ALL SELECT v.id FROM vouchers v JOIN periodic_drafts d ON d.id=v.periodic_review_id AND d.book_id=v.book_id
 WHERE v.source_kind='manual' AND v.review_request_id IS NULL AND v.source_id IS NULL AND v.source_version IS NULL AND d.status='approved' AND d.created_by<>d.approved_by AND v.review_mode='independent' AND v.review_maker_id=d.created_by AND v.review_reviewer_id=d.approved_by AND v.voucher_review_policy_version=0 AND v.description=d.description AND v.voucher_type='转' AND v.relation_kind IS NULL AND v.reversal_of IS NULL;
CREATE VIEW rr_recheck_voucher_headers AS
 SELECT v.id FROM vouchers v JOIN drafts d ON d.id=v.source_id AND d.book_id=v.book_id JOIN effective_draft_review_receipts r ON r.draft_id=d.id AND r.reviewed_version=CASE WHEN d.status='approved' THEN d.version-1 ELSE d.version-2 END JOIN rr_draft_contents dc ON dc.draft_id=d.id AND dc.fingerprint=r.content_fingerprint
 WHERE v.source_kind='draft' AND v.review_request_id IS NULL AND v.periodic_review_id IS NULL AND d.status IN('approved','exported') AND v.source_version=d.version AND v.relation_kind IS NULL AND v.reversal_of IS NULL AND v.description=d.description AND v.review_mode=r.mode AND v.review_maker_id=r.maker_user_id AND v.review_reviewer_id=r.reviewer_user_id AND v.voucher_review_policy_version=r.policy_version
 UNION ALL SELECT v.id FROM vouchers v JOIN manual_review_requests r ON r.id=v.review_request_id AND r.book_id=v.book_id JOIN firm_review_evidence er ON er.kind='manual' AND er.subject_id=r.id AND er.book_id=r.book_id AND er.reviewed_version=r.version AND er.content_fingerprint=r.content_fingerprint JOIN ledger_periods lp ON lp.book_id=r.book_id LEFT JOIN ledger_review_policies p ON p.ledger_id=lp.ledger_id AND p.version=r.policy_version
 WHERE v.source_kind IN('manual','adjustment','reversal') AND v.periodic_review_id IS NULL AND v.source_version IS NULL AND v.source_id IS (CASE WHEN v.source_kind='reversal' THEN v.reversal_of ELSE NULL END) AND r.status='approved' AND v.review_mode=er.mode AND (r.policy_version=0 OR p.version IS NOT NULL) AND v.review_maker_id=er.maker_user_id AND v.review_reviewer_id=er.reviewer_user_id AND v.voucher_review_policy_version=er.policy_version
 AND v.voucher_date=json_extract(r.content_json,'$.voucher_date') AND v.voucher_type=json_extract(r.content_json,'$.voucher_type') AND v.description=json_extract(r.content_json,'$.description') AND v.relation_kind IS json_extract(r.content_json,'$.relation_kind') AND v.reversal_of IS json_extract(r.content_json,'$.original_voucher_id')
 AND v.source_kind=COALESCE(json_extract(r.content_json,'$.relation_kind'),'manual')
 UNION ALL SELECT v.id FROM vouchers v JOIN periodic_drafts d ON d.id=v.periodic_review_id AND d.book_id=v.book_id JOIN firm_review_evidence er ON er.kind='periodic' AND er.subject_id=d.id AND er.book_id=d.book_id AND er.reviewed_version=d.version
 WHERE v.source_kind='manual' AND v.review_request_id IS NULL AND v.source_id IS NULL AND v.source_version IS NULL AND d.status='approved' AND er.maker_user_id<>er.reviewer_user_id AND v.review_mode='independent' AND v.review_maker_id=er.maker_user_id AND v.review_reviewer_id=er.reviewer_user_id AND v.voucher_review_policy_version=er.policy_version AND v.description=d.description AND v.voucher_type='转' AND v.relation_kind IS NULL AND v.reversal_of IS NULL;
DROP VIEW rr_verified_voucher_headers; CREATE VIEW rr_verified_voucher_headers AS SELECT id FROM rr_original_verified_voucher_headers UNION SELECT id FROM rr_recheck_voucher_headers;
DROP TRIGGER rr_draft_update_guard;
CREATE TRIGGER rr_draft_update_guard BEFORE UPDATE ON drafts BEGIN
 SELECT CASE WHEN accounting_transaction_id()='' THEN RAISE(ABORT,'Explicit trusted review transaction required') END;
 SELECT CASE WHEN NEW.id<>OLD.id OR NEW.book_id<>OLD.book_id OR NEW.source_id<>OLD.source_id OR NEW.version<OLD.version THEN RAISE(ABORT,'Draft identity and monotonic version required') END;
 SELECT CASE WHEN OLD.status<>'draft' AND NOT COALESCE((OLD.status='approved' AND NEW.status='exported' AND NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.explanation=OLD.explanation AND NEW.approved_by IS OLD.approved_by AND NEW.approved_at IS OLD.approved_at AND NEW.review_policy_version=OLD.review_policy_version AND NEW.review_reason=OLD.review_reason AND NEW.preparation_claim_reason=OLD.preparation_claim_reason AND NEW.last_manual_edit_version=OLD.last_manual_edit_version AND EXISTS(SELECT 1 FROM effective_draft_review_receipts r JOIN rr_draft_contents c ON c.draft_id=r.draft_id AND c.fingerprint=r.content_fingerprint WHERE r.draft_id=OLD.id AND r.reviewed_version=OLD.version-1)),0) THEN RAISE(ABORT,'Reviewed draft content immutable or legacy receipt missing') END;
 SELECT CASE WHEN NEW.preparation_claim_reason<>OLD.preparation_claim_reason AND (OLD.status<>'draft' OR length(trim(NEW.preparation_claim_reason))<3 OR NEW.version<>OLD.version+1 OR NOT EXISTS(SELECT 1 FROM draft_preparations p WHERE p.draft_id=OLD.id AND p.maker_user_id IS NULL) OR NOT EXISTS(SELECT 1 FROM service_book_user_permissions p WHERE p.book_id=OLD.book_id AND p.user_id=accounting_actor_user() AND p.can_edit)) THEN RAISE(ABORT,'Only unattributed pending preparation can be claimed') END;
 SELECT CASE WHEN OLD.status='draft' AND NEW.status='approved' AND NOT COALESCE((
  NEW.version=OLD.version+1 AND NEW.description=OLD.description AND NEW.explanation=OLD.explanation AND NEW.approved_by=accounting_actor_user() AND NEW.approved_at IS NOT NULL
  AND EXISTS(SELECT 1 FROM service_book_user_permissions p JOIN books b ON b.id=p.book_id WHERE p.book_id=OLD.book_id AND b.firm_id=accounting_actor_firm() AND p.user_id=accounting_actor_user() AND p.can_review)
  AND EXISTS(SELECT 1 FROM draft_preparations p JOIN rr_book_policies pol ON pol.book_id=p.book_id WHERE p.draft_id=OLD.id AND p.version=OLD.version AND p.maker_user_id IS NOT NULL AND pol.version=NEW.review_policy_version AND ((pol.mode='independent' AND p.maker_user_id<>accounting_actor_user()) OR (pol.mode='single_operator' AND length(trim(NEW.review_reason))>=3)))
  AND NOT EXISTS(SELECT 1 FROM business_mappings bm JOIN business_events e ON e.id=bm.event_id WHERE bm.draft_id=OLD.id AND e.applicant_id=accounting_actor_user())
 ),0) THEN RAISE(ABORT,'Current independent reviewer and policy required') END;
 SELECT CASE WHEN OLD.status='draft' AND NEW.status NOT IN('draft','approved') THEN RAISE(ABORT,'Approval receipt required') END;
END;
DROP TRIGGER require_firm_authority_export;
CREATE TRIGGER require_firm_authority_export BEFORE UPDATE ON drafts WHEN OLD.status='approved' AND NEW.status='exported' AND NOT EXISTS(SELECT 1 FROM firm_review_evidence e JOIN rr_draft_contents c ON c.draft_id=e.subject_id AND c.fingerprint=e.content_fingerprint WHERE e.kind='draft' AND e.subject_id=NEW.id AND e.book_id=NEW.book_id AND e.reviewed_version=OLD.version-1) BEGIN SELECT RAISE(ABORT,'Current firm review required'); END;
DROP TRIGGER require_firm_authority_voucher;
CREATE TRIGGER require_firm_authority_voucher BEFORE INSERT ON vouchers WHEN NOT EXISTS(SELECT 1 FROM firm_review_evidence e JOIN legacy_review_sources s ON s.kind=e.kind AND s.subject_id=e.subject_id AND s.book_id=e.book_id AND s.reviewed_version=e.reviewed_version AND s.content_fingerprint=e.content_fingerprint WHERE e.book_id=NEW.book_id AND e.mode=NEW.review_mode AND e.maker_user_id=NEW.review_maker_id AND e.reviewer_user_id=NEW.review_reviewer_id AND e.policy_version=NEW.voucher_review_policy_version AND ((e.kind='draft' AND NEW.source_kind='draft' AND NEW.periodic_review_id IS NULL AND e.subject_id=NEW.source_id) OR (e.kind='manual' AND e.subject_id=NEW.review_request_id) OR (e.kind='periodic' AND e.subject_id=NEW.periodic_review_id))) BEGIN SELECT RAISE(ABORT,'Current firm review proof required'); END;
