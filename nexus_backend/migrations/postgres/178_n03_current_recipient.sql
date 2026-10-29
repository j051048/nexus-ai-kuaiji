-- compatibility: breaking
-- A notification is signable only by the current service-plan contact who
-- retains book read permission. This helper intentionally does not depend on
-- request actor settings: the restricted worker has no impersonated actor.
CREATE OR REPLACE FUNCTION accounting.n03_recipient_eligible(
 target_book TEXT,target_user TEXT,target_firm TEXT,target_company TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(
 SELECT 1 FROM accounting.books b
 JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.service_plan_versions p ON p.book_id=b.id
  AND p.version=(SELECT MAX(version) FROM accounting.service_plan_versions WHERE book_id=b.id)
 JOIN accounting.users u ON u.id=p.contact_user_id AND u.active=1
 JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE b.id=target_book AND b.firm_id=target_firm AND b.company_id=target_company AND u.id=target_user
 AND (m.role IN('owner','admin') OR
  (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance'))))) $$;

CREATE OR REPLACE FUNCTION accounting.n03_notification_current(n accounting.delivery_notifications)
 RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE p RECORD;g RECORD;
BEGIN
 IF n.kind='missing_contact_followup' OR n.restore_epoch IS DISTINCT FROM accounting.n03_restore_epoch()
 OR NOT accounting.n03_review_eligible(n.book_id,n.created_by,n.firm_id)
 OR NOT accounting.n03_recipient_eligible(n.book_id,n.recipient_user_id,n.firm_id,n.company_id)
 THEN RETURN false; END IF;
 SELECT * INTO p FROM accounting.monthly_delivery_packages WHERE id=n.package_id;
 IF NOT FOUND OR (p.firm_id,p.company_id,p.book_id,p.period,p.version,p.manifest_sha256)
   IS DISTINCT FROM (n.firm_id,n.company_id,n.book_id,n.period,n.package_version,n.manifest_sha256)
 OR p.version<>(SELECT MAX(version) FROM accounting.monthly_delivery_packages WHERE book_id=n.book_id AND period=n.period)
 OR p.contact_user_id IS NULL OR p.contact_user_id IS DISTINCT FROM n.recipient_user_id
 OR p.created_by=n.recipient_user_id
 OR EXISTS(SELECT 1 FROM accounting.monthly_delivery_acknowledgements WHERE package_id=p.id)
 OR EXISTS(SELECT 1 FROM accounting.delivery_notifications other WHERE other.id<>n.id
 AND other.package_id=n.package_id AND other.recipient_user_id=n.recipient_user_id
 AND other.state IN('claimed','sending','unknown')) THEN RETURN false; END IF;
 IF n.grant_id IS NULL THEN
  RETURN n.grant_version=0 AND NOT EXISTS(
   SELECT 1 FROM accounting.monthly_delivery_grants WHERE book_id=n.book_id AND period=n.period);
 END IF;
 SELECT * INTO g FROM accounting.monthly_delivery_grants WHERE id=n.grant_id;
 RETURN FOUND AND g.state='active' AND
 (g.firm_id,g.company_id,g.book_id,g.period,g.recipient_user_id,g.version)
 IS NOT DISTINCT FROM (n.firm_id,n.company_id,n.book_id,n.period,n.recipient_user_id,n.grant_version);
END $$;

-- This private predicate exposes no new SQL interface to app/worker users.
REVOKE ALL ON FUNCTION accounting.n03_recipient_eligible(TEXT,TEXT,TEXT,TEXT)
 FROM PUBLIC,accounting_app,accounting_system,accounting_worker;
