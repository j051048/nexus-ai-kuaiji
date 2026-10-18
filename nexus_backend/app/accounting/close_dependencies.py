"""Monotonic, database-maintained revisions for close and report evidence.

Counters are scoped to a book, its stable ledger and the selected report
schema. A change followed by restoring the original value is still a change.
Only database triggers can advance these counters in PostgreSQL.
"""


def close_dependencies(conn, book_id, template_id):
    ledger = conn.execute(
        "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
    ).fetchone()
    if not ledger:
        from .domain import AccountingError

        raise AccountingError("账套未关联长期账套", 409)
    scopes = {"book": book_id, "ledger": ledger["ledger_id"], "template": template_id}
    result = {"version": 1}
    for kind, scope_id in scopes.items():
        row = conn.execute(
            "SELECT revision FROM close_dependency_revisions WHERE scope_kind=? AND scope_id=?",
            (kind, scope_id),
        ).fetchone()
        result[kind] = {"id": scope_id, "revision": int(row[0]) if row else 0}
    return result
