"""外部办理结果：人工登记，系统不代申报、不改账。

A filing record is evidence that the firm (or the client) actually filed or paid
outside the system. It never flips a workpaper to "reviewed" and never changes
vouchers: those still need their own independent review. Corrections and voids
are appended as new rows which point at the row they replace; the historical
row keeps its original bytes and can always be reconstructed.
"""

import re
from datetime import date
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import now
from .workpaper import _period

KINDS = {"vat": "增值税", "surtax": "附加税费"}
STATUSES = {"filed": "已申报", "paid": "已缴款"}
ENTRY_KINDS = {"registration", "correction", "void"}
AMOUNT_CHECKS = {"match", "mismatch", "no_workpaper"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _attachment(row):
    if not row["attachment_id"]:
        return None
    return {
        "id": row["attachment_id"],
        "filename": row["attachment_filename"],
        "media_type": row["attachment_media_type"],
        "sha256": row["attachment_sha256"],
        "size_bytes": int(row["attachment_size_bytes"]),
        "created_at": row["attachment_created_at"],
    }


def _present(row, *, superseded=(), current=True):
    return {
        "id": row["id"],
        "period": row["period"],
        "kind": row["kind"],
        "kind_label": KINDS.get(row["kind"], row["kind"]),
        "status": row["status"],
        "status_label": STATUSES.get(row["status"], row["status"]),
        "entry_kind": row["entry_kind"],
        "supersedes_id": row["supersedes_id"],
        "correction_reason": row["correction_reason"],
        "external_ref": row["external_ref"],
        "occurred_at": row["occurred_at"],
        "amount_cents": int(row["amount_cents"]),
        "evidence_note": row["evidence_note"],
        "workpaper_id": row["workpaper_id"],
        "workpaper_version": row["workpaper_version"],
        "workpaper_status": row["workpaper_status"],
        "workpaper_digest": row["workpaper_digest"],
        "amount_check": row["amount_check"],
        "amount_matches_workpaper": row["amount_check"] == "match",
        "current": bool(current and row["id"] not in superseded and row["entry_kind"] != "void"),
        "attachment": _attachment(row),
        "created_at": row["created_at"],
    }


SELECT = (
    "SELECT f.*,a.id AS attachment_id,a.filename AS attachment_filename,"
    "a.media_type AS attachment_media_type,a.sha256 AS attachment_sha256,"
    "a.size_bytes AS attachment_size_bytes,a.created_at AS attachment_created_at "
    "FROM tax_filings f LEFT JOIN tax_filing_attachments a ON a.id=("
    "SELECT x.id FROM tax_filing_attachments x WHERE x.filing_id=f.id "
    "ORDER BY x.created_at DESC,x.id DESC LIMIT 1) "
)


def _rows(conn, where, args):
    rows = conn.execute(SELECT + where + " ORDER BY f.created_at DESC,f.id DESC", args).fetchall()
    superseded = {row["supersedes_id"] for row in rows if row["supersedes_id"]}
    return [_present(row, superseded=superseded) for row in rows]


def _latest_workpaper(conn, book_id, period):
    return conn.execute(
        "SELECT * FROM tax_workpapers WHERE book_id=? AND period=? ORDER BY version DESC LIMIT 1",
        (book_id, period),
    ).fetchone()


def _amount_check(paper, kind, amount):
    if not paper:
        return "no_workpaper"
    expected = int(paper["tax_cents"] if kind == "vat" else paper["surtax_cents"])
    return "match" if expected == amount else "mismatch"


def _predecessor(conn, filing_id, book_id, period, kind):
    if not isinstance(filing_id, str) or not filing_id:
        raise AccountingError("更正或作废必须指明被替代的回执", 422)
    old = conn.execute(
        "SELECT * FROM tax_filings WHERE id=? AND book_id=?", (filing_id, book_id)
    ).fetchone()
    if not old or old["period"] != period or old["kind"] != kind:
        raise AccountingError("被替代的回执不属于当前账套、期间或税种", 409)
    if old["entry_kind"] == "void":
        raise AccountingError("已作废的回执不能再更正或作废", 409)
    successor = conn.execute(
        "SELECT 1 FROM tax_filings WHERE supersedes_id=?", (filing_id,)
    ).fetchone()
    if successor:
        raise AccountingError("该回执已有更新版本，请基于最新记录处理", 409)
    return old


def record(service, actor, payload):
    """Record one external filing/payment row. Reviewers only, append-only."""
    scope = service._scope(actor, payload.get("book_id"), "review")
    period = _period(payload.get("period") or scope["period"])
    if period != scope["period"]:
        raise AccountingError("办理期间必须与账套期间一致", 422)
    kind = payload.get("kind")
    if kind not in KINDS:
        raise AccountingError("税种只能是 vat 或 surtax", 422)
    status = payload.get("status")
    if status not in STATUSES:
        raise AccountingError("办理结果只能是 filed 或 paid", 422)
    entry_kind = payload.get("entry_kind") or "registration"
    if entry_kind not in ENTRY_KINDS:
        raise AccountingError("办理记录类型只能是 registration、correction 或 void", 422)
    reference = str(payload.get("external_ref") or "").strip()
    if not 3 <= len(reference) <= 100:
        raise AccountingError("外部流水号或回执编号需 3-100 字", 422)
    occurred_at = str(payload.get("occurred_at") or "").strip()
    if not DATE.match(occurred_at):
        raise AccountingError("办理日期需为 YYYY-MM-DD", 422)
    try:
        date.fromisoformat(occurred_at)
    except ValueError:
        raise AccountingError("办理日期不是有效日历日期", 422) from None
    amount = payload.get("amount_cents", 0)
    if type(amount) is not int or amount < 0:
        raise AccountingError("金额必须是非负整数分", 422)
    note = str(payload.get("evidence_note") or "").strip()
    if len(note) > 500:
        raise AccountingError("证据说明不能超过 500 字", 422)
    reason = str(payload.get("correction_reason") or "").strip()
    if entry_kind == "registration":
        if payload.get("supersedes_id") or reason:
            raise AccountingError("首次登记不能替代历史回执", 422)
        supersedes_id = None
    else:
        if not 3 <= len(reason) <= 1000:
            raise AccountingError("更正或作废原因需 3-1000 字", 422)
        supersedes_id = payload.get("supersedes_id")

    with service.db.transaction(
        write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
    ) as conn:
        book = service.access.book(conn, actor, payload["book_id"], "review")
        supersedes = None
        if entry_kind in {"correction", "void"}:
            supersedes = _predecessor(conn, supersedes_id, book["id"], period, kind)
            if entry_kind == "void" and status != supersedes["status"]:
                raise AccountingError("作废记录须保留被替代回执的原办理结果", 422)
        existing = conn.execute(
            "SELECT 1 FROM tax_filings WHERE book_id=? AND period=? AND kind=? AND status=? "
            "AND external_ref=?",
            (book["id"], period, kind, status, reference),
        ).fetchone()
        if existing:
            raise AccountingError("同一回执已登记，请勿重复提交", 409)
        paper = _latest_workpaper(conn, book["id"], period)
        filing_id = "fil_" + uuid4().hex[:16]
        created_at = now()
        conn.execute(
            "INSERT INTO tax_filings(id,firm_id,company_id,book_id,period,kind,status,"
            "external_ref,occurred_at,amount_cents,evidence_note,created_by,created_at,"
            "entry_kind,supersedes_id,workpaper_id,workpaper_version,workpaper_digest,"
            "workpaper_status,amount_check,correction_reason) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                entry_kind,
                supersedes["id"] if supersedes else None,
                paper["id"] if paper else None,
                int(paper["version"]) if paper else None,
                paper["source_digest"] if paper else None,
                paper["status"] if paper else None,
                _amount_check(paper, kind, amount),
                reason,
            ),
        )
        service._audit(
            conn,
            actor,
            book,
            "tax.filing.record",
            filing_id,
            {
                "period": period,
                "kind": kind,
                "status": status,
                "amount_cents": amount,
                "entry_kind": entry_kind,
                "supersedes_id": supersedes["id"] if supersedes else None,
                "workpaper_version": int(paper["version"]) if paper else None,
                "amount_check": _amount_check(paper, kind, amount),
            },
        )
        row = conn.execute(SELECT + "WHERE f.id=?", (filing_id,)).fetchone()
    return _present(row)


def attach(uploads, service, actor, filing_id, upload_id):
    """Bind one already-validated private original to an existing filing."""
    with service.db.transaction(actor=actor) as conn:
        filing = conn.execute(
            "SELECT * FROM tax_filings WHERE id=? AND firm_id=?", (filing_id, actor.firm_id)
        ).fetchone()
        if not filing:
            raise AccountingError("办理记录不存在或无权访问", 404)
        service.access.book(conn, actor, filing["book_id"], "review")

    def register(conn, stored):
        current = conn.execute(
            "SELECT * FROM tax_filings WHERE id=? AND firm_id=?", (filing_id, actor.firm_id)
        ).fetchone()
        if not current or current["entry_kind"] == "void":
            raise AccountingError("作废回执不能继续绑定原件", 409)
        if conn.execute("SELECT 1 FROM tax_filings WHERE supersedes_id=?", (filing_id,)).fetchone():
            raise AccountingError("该回执已有更正版本，请为最新记录补件", 409)
        book = service.access.book(conn, actor, current["book_id"], "review")
        if conn.execute(
            "SELECT 1 FROM tax_filing_attachments WHERE filing_id=?", (filing_id,)
        ).fetchone():
            raise AccountingError("该回执已绑定原件；补件请登记新的更正记录", 409)
        conn.execute(
            "INSERT INTO tax_filing_attachments(id,filing_id,firm_id,company_id,book_id,"
            "storage_key,filename,media_type,sha256,size_bytes,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                stored["id"],
                filing_id,
                actor.firm_id,
                book["company_id"],
                book["id"],
                stored["storage_key"],
                stored["filename"],
                stored["media_type"],
                stored["sha256"],
                int(stored["size_bytes"]),
                actor.user_id,
                now(),
            ),
        )
        return {"filing_id": filing_id, "attachment_id": stored["id"]}

    return uploads.consume(actor, upload_id, "tax_filing_attachment", filing_id, register)


def attachment_for(service, actor, filing_id, attachment_id):
    with service.db.transaction(actor=actor) as conn:
        row = conn.execute(
            "SELECT a.* FROM tax_filing_attachments a JOIN tax_filings f ON f.id=a.filing_id "
            "WHERE a.id=? AND a.filing_id=? AND a.firm_id=?",
            (attachment_id, filing_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("回执原件不存在或无权访问", 404)
        service.access.book(conn, actor, row["book_id"], "read")
        return dict(row)


def list_for(service, actor, book_id, period=None):
    scope = service._scope(actor, book_id, "read")
    period = _period(period or scope["period"])
    with service.db.transaction(actor=actor) as conn:
        service.access.book(conn, actor, book_id, "read")
        return _rows(conn, "WHERE f.book_id=? AND f.period=?", (book_id, period))


def current_for(conn, book_id, period):
    """Read-only projection used by monthly packages; caller already checked scope."""
    return [
        row
        for row in _rows(conn, "WHERE f.book_id=? AND f.period=?", (book_id, period))
        if row["current"]
    ]


def evidence_for(conn, book_id, period):
    """Current state plus the full append-only chain, frozen into a package."""
    rows = _rows(conn, "WHERE f.book_id=? AND f.period=?", (book_id, period))
    return {"current": [row for row in rows if row["current"]], "history": rows}


def summary(rows):
    """Latest current state per kind; registration never implies the other kind."""
    current = [row for row in rows if row["current"]]
    latest = {}
    for row in current:
        latest.setdefault(row["kind"], row)
    return {
        "external_result": "registered" if current else "none",
        "filing_state": "paid"
        if any(row["status"] == "paid" for row in current)
        else ("filed" if current else "not_filed"),
        "filings": rows,
        "current_filings": current,
        "by_kind": {kind: latest[kind] for kind in latest},
    }
