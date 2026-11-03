"""Bound read queries for handoff sources, shared by queue and exact-source reads.

Only persisted pending facts are selected here. The service still checks current
book/company access and computes the presentation. PostgreSQL retains RLS and
the native mutation guards; no permissions are cached or inferred by this layer.
"""


def batch_rows(conn, firm_id, source_id=None):
    return conn.execute(
        "SELECT i.id,i.batch_id,i.company_id,i.company_name,i.attempt,i.error_code,"
        "i.error_detail,i.updated_at,i.result_json,b.request_json,b.kind AS batch_kind "
        "FROM batch_operation_items i JOIN batch_operations b ON b.id=i.batch_id "
        "WHERE i.firm_id=? AND i.status='failed' AND b.firm_id=? "
        + ("AND i.id=? " if source_id is not None else "")
        + "ORDER BY i.updated_at DESC,i.id",
        (firm_id, firm_id) + ((source_id,) if source_id is not None else ()),
    ).fetchall()


def notification_rows(conn, firm_id, source_id=None):
    return conn.execute(
        "SELECT n.id,n.book_id,n.period,n.package_id,n.package_version,n.manifest_sha256,"
        "n.attempts,n.state,n.updated_at,b.name AS book_name,b.company_id,c.name AS company_name "
        "FROM delivery_notifications n JOIN books b ON b.id=n.book_id "
        "JOIN companies c ON c.id=b.company_id "
        "WHERE n.firm_id=? AND n.state='unknown' "
        + ("AND n.id=? " if source_id is not None else "")
        + "ORDER BY n.updated_at DESC,n.id",
        (firm_id,) + ((source_id,) if source_id is not None else ()),
    ).fetchall()


def delivery_rows(conn, firm_id, manager, user_id, source_id=None):
    return conn.execute(
        "SELECT p.id,p.version,p.book_id,p.period,p.manifest_sha256,p.created_at,"
        "b.name AS book_name,b.company_id,c.name AS company_name "
        "FROM monthly_delivery_packages p JOIN books b ON b.id=p.book_id "
        "JOIN companies c ON c.id=b.company_id "
        "WHERE p.firm_id=? AND NOT EXISTS(SELECT 1 FROM monthly_delivery_acknowledgements a "
        "WHERE a.package_id=p.id) AND p.version=(SELECT MAX(p2.version) FROM "
        "monthly_delivery_packages p2 WHERE p2.book_id=p.book_id AND p2.period=p.period) "
        "AND (? OR EXISTS(SELECT 1 FROM handoff_tasks h WHERE h.firm_id=p.firm_id "
        "AND h.source_kind='delivery_package' AND h.source_id=p.id "
        "AND h.assignee_id=? AND h.superseded_at IS NULL)) "
        + ("AND p.id=? " if source_id is not None else "")
        + "ORDER BY p.created_at DESC,p.id",
        (firm_id, manager, user_id) + ((source_id,) if source_id is not None else ()),
    ).fetchall()


def tax_rows(conn, firm_id, source_id=None):
    return conn.execute(
        "SELECT w.id,w.version,w.source_digest,w.book_id,w.period,w.created_by,w.status,"
        "b.name AS book_name,b.company_id,c.name AS company_name "
        "FROM tax_workpapers w JOIN books b ON b.id=w.book_id "
        "JOIN companies c ON c.id=w.company_id "
        "WHERE w.status='draft' AND b.firm_id=? "
        "AND w.version=(SELECT MAX(w2.version) FROM tax_workpapers w2 "
        "WHERE w2.book_id=w.book_id AND w2.period=w.period) "
        + ("AND w.id=? " if source_id is not None else "")
        + "ORDER BY w.period DESC,w.id",
        (firm_id,) + ((source_id,) if source_id is not None else ()),
    ).fetchall()
