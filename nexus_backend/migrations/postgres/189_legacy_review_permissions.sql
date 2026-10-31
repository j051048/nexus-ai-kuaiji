-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
CREATE OR REPLACE FUNCTION accounting.legacy_review_insert_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE company TEXT; BEGIN
 SELECT company_id INTO company FROM accounting.books WHERE id=NEW.book_id AND firm_id=accounting.actor_firm();
 IF company IS NULL THEN RAISE EXCEPTION 'Review source not visible' USING ERRCODE='42501'; END IF;
 PERFORM accounting.lock_company_write(accounting.actor_firm(),company);
 IF TG_TABLE_NAME='legacy_review_preparations' THEN
 IF NOT EXISTS(SELECT 1 FROM books b JOIN rr_book_permissions bp ON bp.book_id=b.id WHERE b.id=NEW.book_id AND b.firm_id=accounting.actor_firm() AND bp.user_id=accounting.actor_user() AND bp.can_edit AND NEW.maker_user_id=accounting.actor_user()) OR NOT EXISTS(SELECT 1 FROM legacy_review_sources_private s JOIN rr_book_policies pol ON pol.book_id=s.book_id
 JOIN books b ON b.id=s.book_id JOIN periods pr ON pr.book_id=b.id AND pr.period=b.period
 WHERE s.kind=NEW.kind AND s.subject_id=NEW.subject_id AND s.book_id=NEW.book_id
 AND s.source_version=NEW.source_version AND s.reviewed_version=NEW.reviewed_version AND s.content_revision=NEW.content_revision
 AND s.content_fingerprint=NEW.content_fingerprint AND pol.version=NEW.policy_version
 AND NEW.mode=CASE WHEN NEW.kind='periodic' THEN 'independent' ELSE pol.mode END
 AND pr.status='open' AND NOT EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM review_authority_snapshots a WHERE a.subject_kind=s.kind AND a.subject_id=s.subject_id AND a.book_id=s.book_id AND a.reviewed_version=s.reviewed_version AND a.proof_kind='review_time')
 AND NOT EXISTS(SELECT 1 FROM vouchers v WHERE v.book_id=s.book_id AND ((s.kind='draft' AND v.source_kind='draft' AND v.source_id=s.subject_id) OR (s.kind='manual' AND v.review_request_id=s.subject_id) OR (s.kind='periodic' AND v.periodic_review_id=s.subject_id)))) THEN RAISE EXCEPTION 'Current preparation, source, policy and open period required' USING ERRCODE='42501'; END IF;
 NEW.created_at:=clock_timestamp()::text;
 ELSE
 IF NOT EXISTS(SELECT 1 FROM legacy_review_preparations p JOIN legacy_review_sources s ON s.kind=p.kind AND s.subject_id=p.subject_id AND s.book_id=p.book_id
 JOIN rr_book_permissions bp ON bp.book_id=p.book_id JOIN books b ON b.id=p.book_id
 JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=accounting.actor_user()
 WHERE p.id=NEW.request_id AND NEW.book_id=p.book_id AND bp.user_id=accounting.actor_user() AND bp.can_review AND NEW.reviewer_user_id=accounting.actor_user()
 AND NEW.role_snapshot=m.role AND m.role IN('owner','admin','reviewer') AND b.firm_id=accounting.actor_firm()
 AND (p.mode='single_operator' OR (p.maker_user_id<>NEW.reviewer_user_id AND (s.known_maker IS NULL OR s.known_maker<>NEW.reviewer_user_id)))
 AND NOT EXISTS(SELECT 1 FROM business_mappings bm JOIN business_events be ON be.id=bm.event_id WHERE p.kind='draft' AND bm.draft_id=p.subject_id AND be.applicant_id=NEW.reviewer_user_id)
 AND EXISTS(SELECT 1 FROM legacy_review_sources_private s JOIN rr_book_policies pol ON pol.book_id=s.book_id
 JOIN books b ON b.id=s.book_id JOIN periods pr ON pr.book_id=b.id AND pr.period=b.period
 WHERE s.kind=p.kind AND s.subject_id=p.subject_id AND s.book_id=p.book_id
 AND s.source_version=p.source_version AND s.reviewed_version=p.reviewed_version AND s.content_revision=p.content_revision
 AND s.content_fingerprint=p.content_fingerprint AND pol.version=p.policy_version
 AND p.mode=CASE WHEN p.kind='periodic' THEN 'independent' ELSE pol.mode END
 AND pr.status='open' AND NOT EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM review_authority_snapshots a WHERE a.subject_kind=s.kind AND a.subject_id=s.subject_id AND a.book_id=s.book_id AND a.reviewed_version=s.reviewed_version AND a.proof_kind='review_time')
 AND NOT EXISTS(SELECT 1 FROM vouchers v WHERE v.book_id=s.book_id AND ((s.kind='draft' AND v.source_kind='draft' AND v.source_id=s.subject_id) OR (s.kind='manual' AND v.review_request_id=s.subject_id) OR (s.kind='periodic' AND v.periodic_review_id=s.subject_id))))) THEN RAISE EXCEPTION 'Current firm review and exact source required' USING ERRCODE='42501'; END IF;
 NEW.reviewed_at:=clock_timestamp()::text;
 END IF; RETURN NEW; END $$;
