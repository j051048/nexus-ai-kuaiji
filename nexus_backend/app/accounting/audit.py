"""Shared append-only audit writer for read/download events."""

from __future__ import annotations

import json

from .service import now


def write_download_audit(
    conn,
    actor,
    *,
    action: str,
    object_id: str,
    company_id: str | None = None,
    book_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Record one successful read/download without file bytes.

    Callers must write this row in the same transaction that authorizes the
    object and before returning its content. A failed audit insert therefore
    fails the download closed.
    """
    conn.execute(
        "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at,company_id) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            actor.firm_id,
            book_id,
            actor.user_id,
            action,
            object_id,
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            now(),
            company_id,
        ),
    )
