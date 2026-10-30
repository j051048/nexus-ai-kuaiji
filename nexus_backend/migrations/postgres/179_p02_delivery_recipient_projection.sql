-- compatibility: incremental
-- Reviewers need a bounded membership fact, not visibility of the company
-- directory. Current book review authority is required for every invocation.
-- Configuring a recipient is allowed before a package/service plan exists;
-- sending still separately requires the frozen and current signable contact.
CREATE OR REPLACE FUNCTION accounting.delivery_recipient_member(target_book TEXT,target_user TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_review_allowed(target_book) AND EXISTS(
  SELECT 1 FROM accounting.books b
  JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
  JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id
   AND fc.company_id=b.company_id AND fc.status='active'
  JOIN accounting.company_memberships cm ON cm.company_id=b.company_id
   AND cm.user_id=target_user
  JOIN accounting.users u ON u.id=cm.user_id AND u.active=1
  WHERE b.id=target_book AND b.firm_id=accounting.actor_firm()
 )
$$;
REVOKE ALL ON FUNCTION accounting.delivery_recipient_member(TEXT,TEXT)
 FROM PUBLIC,accounting_system,accounting_worker,accounting_projection;
GRANT EXECUTE ON FUNCTION accounting.delivery_recipient_member(TEXT,TEXT) TO accounting_app;
