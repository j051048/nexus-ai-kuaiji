"""外部办理结果：人工登记，系统不代申报、不改账。

A filing record is evidence that the firm (or the client) actually filed or paid
outside the system. It never flips a workpaper to "reviewed" and never changes
vouchers: those still need their own independent review.
"""

import re
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import now
from .workpaper import _period

KINDS = {"vat": "增值税", "surtax": "附加税费"}
STATUSES = {"filed": "已申报", "paid": "已缴款"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _present(row):
    return {
        "id": row["id"],
        "period": row["period"],
        "kind": row["kind"],
        "kind_label": KINDS.get(row["kind"], row["kind"]),
        "status": row["status"],
        "status_label": STATUSES.get(row["status"], row["status"]),
        "external_ref": row["external_ref"],
        "occurred_at": row["occurred_at"],
        "amount_cents": int(row["amount_cents"]),
        "evidence_note": row["evidence_note"],
        "created_at": row["created_at"],
    }


def record(service, actor, payload):
    """Record one external filing/payment row. Reviewers only, append-only."""
    scope = service._scope(actor, payload.get("book_id"), "review")
    period = _period(payload.get("period") or scope["period"])
    kind = payload.get("kind")
    if kind not in KINDS:
        raise AccountingError("税种只能是 vat 或 surtax", 422)
    status = payload.get("status")
    if status not in STATUSES:
        raise AccountingError("办理结果只能是 filed 或 paid", 422)
    reference = str(payload.get("external_ref") or "").strip()
    if not 3 <= len(reference) <= 100:
        raise AccountingError("外部流水号或回执编号需 3-100 字", 422)
    occurred_at = str(payload.get("occurred_at") or "").strip()
    if not DATE.match(occurred_at):
        raise AccountingError("办理日期需为 YYYY-MM-DD", 422)
    amount = payload.get("amount_cents", 0)
    if type(amount) is not int or amount < 0:
        raise AccountingError("金额必须是非负整数分", 422)
    note = str(payload.get("evidence_note") or "").strip()
    if len(note) > 500:
        raise AccountingError("证据说明不能超过 500 字", 422)

    with service.db.transaction(
        write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
    ) as conn:
        book = service.access.book(conn, actor, payload["book_id"], "review")
        existing = conn.execute(
            "SELECT 1 FROM tax_filings WHERE book_id=? AND period=? AND kind=? AND status=? "
            "AND external_ref=?",
            (book["id"], period, kind, status, reference),
        ).fetchone()
        if existing:
            raise AccountingError("同一回执已登记，请勿重复提交", 409)
        filing_id = "fil_" + uuid4().hex[:16]
        created_at = now()
        conn.execute(
            "INSERT INTO tax_filings(id,firm_id,company_id,book_id,period,kind,status,"
            "external_ref,occurred_at,amount_cents,evidence_note,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                filing_id,
                actor.firm_id,
                book["company_id"],
                book["id"],
                period,
                kind,
                status,
                reference,
                occurred_at,
                amount,
                note,
                actor.user_id,
                created_at,
            ),
        )
        service._audit(
            conn,
            actor,
            book,
            "tax.filing.record",
            filing_id,
            {"period": period, "kind": kind, "status": status, "amount_cents": amount},
        )
        row = conn.execute("SELECT * FROM tax_filings WHERE id=?", (filing_id,)).fetchone()
    return _present(row)


def list_for(service, actor, book_id, period=None):
    scope = service._scope(actor, book_id, "read")
    period = _period(period or scope["period"])
    with service.db.transaction(actor=actor) as conn:
        service.access.book(conn, actor, book_id, "read")
        rows = conn.execute(
            "SELECT * FROM tax_filings WHERE book_id=? AND period=? "
            "ORDER BY occurred_at DESC, created_at DESC",
            (book_id, period),
        ).fetchall()
    return [_present(row) for row in rows]


def summary(rows):
    """Latest state per kind; registration never implies the other kind."""
    latest = {}
    for row in rows:
        latest.setdefault(row["kind"], row)
    return {
        "external_result": "registered" if rows else "none",
        "filing_state": "paid"
        if any(row["status"] == "paid" for row in rows)
        else ("filed" if rows else "not_filed"),
        "filings": rows,
        "by_kind": {kind: latest[kind] for kind in latest},
    }
