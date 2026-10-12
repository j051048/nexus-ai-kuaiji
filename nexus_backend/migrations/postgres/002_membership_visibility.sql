-- A company administrator must detect the firm's existing staff identity when
-- managing company book grants. Without this limited metadata visibility,
-- SELECT-based authorization would mistake a hidden staff row for absence.
CREATE FUNCTION accounting.company_manager_sees_member(target_firm TEXT,target_user TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients fc WHERE fc.firm_id=target_firm AND fc.status='active'
  AND accounting.company_manager(fc.company_id) AND (
   EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=fc.company_id AND cm.user_id=target_user)
   OR EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id
    WHERE b.firm_id=target_firm AND b.company_id=fc.company_id AND ba.user_id=target_user)
  )
 )
$$;
REVOKE ALL ON FUNCTION accounting.company_manager_sees_member(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.company_manager_sees_member(TEXT,TEXT) TO accounting_app;
DROP POLICY memberships_read ON accounting.memberships;
CREATE POLICY memberships_read ON accounting.memberships FOR SELECT TO accounting_app
 USING((user_id=accounting.actor_user() AND accounting.actor_active()) OR accounting.firm_manager(firm_id)
 OR accounting.company_manager_sees_member(firm_id,user_id));
