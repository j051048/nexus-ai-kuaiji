-- compatibility: incremental
-- NEXT04: controlled read-model adapters for batch failures, unknown delivery
-- notifications and unsigned monthly packages. The source domain still owns
-- its state; this guard only accepts a projection that still matches it.
CREATE OR REPLACE FUNCTION accounting.dcp_handoff_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD; w RECORD; i RECORD; n RECORD; p RECORD; target_role TEXT;
BEGIN
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
 OR NOT accounting.actor_active() OR NOT accounting.firm_manager(NEW.firm_id)
 OR NOT accounting.book_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Current handoff management authority required' USING ERRCODE='42501';
 END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
 IF NOT accounting.actor_active() OR NOT accounting.firm_manager(NEW.firm_id)
 OR NOT accounting.book_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Handoff authority changed while waiting' USING ERRCODE='42501';
 END IF;
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['superseded_at','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['superseded_at','updated_at'])
   OR OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL THEN
   RAISE EXCEPTION 'Handoff history cannot be rewritten' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 IF NEW.created_by IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'Handoff creator must be current actor' USING ERRCODE='42501';
 END IF;
 IF NEW.status<>'assigned' OR NEW.superseded_at IS NOT NULL
 OR length(trim(NEW.reason)) NOT BETWEEN 3 AND 1000
 OR NEW.source_fingerprint !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'Unsupported handoff contract' USING ERRCODE='23514';
 END IF;
 IF NEW.source_kind='tax_workpaper' THEN
  IF NEW.role<>'reviewer' THEN
   RAISE EXCEPTION 'Unsupported handoff contract' USING ERRCODE='23514';
  END IF;
  SELECT * INTO w FROM accounting.tax_workpapers WHERE id=NEW.source_id;
  IF NOT FOUND OR w.book_id<>NEW.book_id OR w.firm_id<>NEW.firm_id OR w.status<>'draft'
  OR w.version<>NEW.source_version OR w.source_digest IS DISTINCT FROM NEW.source_fingerprint THEN
   RAISE EXCEPTION 'Handoff source identity is stale or foreign' USING ERRCODE='23514';
  END IF;
  IF NEW.assignee_id IS NULL OR NEW.assignee_id=w.created_by
  OR NOT EXISTS(SELECT 1 FROM accounting.rr_book_permissions p
   WHERE p.book_id=NEW.book_id AND p.user_id=NEW.assignee_id AND p.can_review) THEN
   RAISE EXCEPTION 'Independent current reviewer required' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.source_kind='batch_item' THEN
  IF NEW.role<>'ops' THEN
   RAISE EXCEPTION 'Unsupported handoff contract' USING ERRCODE='23514';
  END IF;
  SELECT * INTO i FROM accounting.batch_operation_items WHERE id=NEW.source_id;
  IF NOT FOUND OR i.firm_id<>NEW.firm_id OR i.status<>'failed'
  OR (i.result_json::jsonb->>'book_id')<>NEW.book_id
  OR GREATEST(i.attempt,1)<>NEW.source_version THEN
   RAISE EXCEPTION 'Batch source identity is stale or foreign' USING ERRCODE='23514';
  END IF;
  IF NEW.assignee_id IS NULL OR NOT EXISTS(
   SELECT 1 FROM accounting.memberships m WHERE m.firm_id=NEW.firm_id
   AND m.user_id=NEW.assignee_id AND m.role IN ('owner','admin')) THEN
   RAISE EXCEPTION 'Current firm manager required' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.source_kind='notification_investigation' THEN
  IF NEW.role<>'ops' THEN
   RAISE EXCEPTION 'Unsupported handoff contract' USING ERRCODE='23514';
  END IF;
  SELECT * INTO n FROM accounting.delivery_notifications WHERE id=NEW.source_id;
  IF NOT FOUND OR n.firm_id<>NEW.firm_id OR n.book_id<>NEW.book_id
  OR n.state<>'unknown' OR (n.attempts+1)<>NEW.source_version THEN
   RAISE EXCEPTION 'Notification source identity is stale or foreign' USING ERRCODE='23514';
  END IF;
  IF NEW.assignee_id IS NULL OR NOT EXISTS(
   SELECT 1 FROM accounting.memberships m WHERE m.firm_id=NEW.firm_id
   AND m.user_id=NEW.assignee_id AND m.role IN ('owner','admin')) THEN
   RAISE EXCEPTION 'Current firm manager required' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.source_kind='delivery_package' THEN
  IF NEW.role<>'delivery' THEN
   RAISE EXCEPTION 'Unsupported handoff contract' USING ERRCODE='23514';
  END IF;
  SELECT * INTO p FROM accounting.monthly_delivery_packages WHERE id=NEW.source_id;
  IF NOT FOUND OR p.firm_id<>NEW.firm_id OR p.book_id<>NEW.book_id
  OR p.version<>NEW.source_version OR p.manifest_sha256 IS DISTINCT FROM NEW.source_fingerprint
  OR EXISTS(SELECT 1 FROM accounting.monthly_delivery_acknowledgements a WHERE a.package_id=p.id) THEN
   RAISE EXCEPTION 'Delivery package identity is stale or foreign' USING ERRCODE='23514';
  END IF;
  IF NEW.assignee_id IS NULL OR NOT EXISTS(
   SELECT 1 FROM accounting.memberships m WHERE m.firm_id=NEW.firm_id
   AND m.user_id=NEW.assignee_id AND m.role IN ('owner','admin','reviewer')) THEN
   RAISE EXCEPTION 'Current delivery staff required' USING ERRCODE='42501';
  END IF;
 ELSE
  RAISE EXCEPTION 'Unsupported handoff contract' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM accounting.handoff_tasks t WHERE t.source_kind=NEW.source_kind
  AND t.source_id=NEW.source_id AND t.source_version=NEW.source_version AND t.role=NEW.role
  AND t.superseded_at IS NULL) THEN
  RAISE EXCEPTION 'Active assignment already exists' USING ERRCODE='23505';
 END IF;
 IF NEW.due_on IS NOT NULL THEN
  BEGIN
   IF NEW.due_on !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR to_char(NEW.due_on::date,'YYYY-MM-DD')<>NEW.due_on THEN
    RAISE EXCEPTION 'Invalid due date' USING ERRCODE='23514';
   END IF;
  EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
   RAISE EXCEPTION 'Invalid due date' USING ERRCODE='23514';
  END;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.dcp_handoff_guard() FROM PUBLIC;
