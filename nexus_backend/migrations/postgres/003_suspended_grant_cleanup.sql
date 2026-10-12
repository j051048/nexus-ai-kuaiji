-- Firm managers must be able to revoke stale grants even while a customer is
-- suspended. This changes authorization metadata only; book_allowed remains
-- active-client scoped for books, sources, drafts, periods and exports.
CREATE OR REPLACE FUNCTION accounting.book_manager(target TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=target AND b.firm_id=accounting.actor_firm()
 AND (accounting.firm_manager(b.firm_id) OR accounting.company_manager(b.company_id)))
$$;
