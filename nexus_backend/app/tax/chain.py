"""B8：规费链路只读统一视图。

一线串联「规费批次 → 驾驶员应收 → 银行核对与核销 → 正式凭证 → 报表快照」，
每一段都明确回答「有没有、依据是什么、缺在哪」。这是**只读投影**：不修数据、
不补凭证、不猜匹配，缺段就如实写缺失原因，让会计据此去补。

设计取舍：段与段之间用真实外键/证据引用连接（`fee_receivable_allocations.bank_source_id`
指银行来源、`bank_statement_line_id` 指流水行、`voucher_sources.source_kind='draft'`
指正式凭证），不做「按金额相近」这类推断连接——那会把两笔相似业务连成一条假链路。
"""

from __future__ import annotations

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError, Actor

PRESENT = "present"
MISSING = "missing"


def _segment(key: str, label: str, items: list[dict], reason: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "state": PRESENT if items else MISSING,
        "count": len(items),
        "items": items,
        "reason": "" if items else reason,
    }


class FeeChainView:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    def chain(self, actor: Actor, book_id: str, batch_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            batch = conn.execute(
                "SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not batch:
                raise AccountingError("规费批次不存在或不属于当前账套", 404)
            receivables = conn.execute(
                "SELECT * FROM fee_receivable_items WHERE batch_id=? AND book_id=? "
                "ORDER BY batch_row_id",
                (batch_id, book_id),
            ).fetchall()
            receivable_ids = [row["id"] for row in receivables]
            allocations = (
                conn.execute(
                    "SELECT * FROM fee_receivable_allocations WHERE receivable_id IN ("
                    + ",".join("?" for _ in receivable_ids)
                    + ") AND reversed_at IS NULL ORDER BY created_at,id",
                    tuple(receivable_ids),
                ).fetchall()
                if receivable_ids
                else []
            )
            bank_ids = sorted(
                {row["bank_source_id"] for row in allocations if row["bank_source_id"]}
            )
            batch_voucher = (
                conn.execute(
                    "SELECT v.id,v.voucher_no,v.voucher_date,v.status,d.id AS draft_id,"
                    "d.status AS draft_status "
                    "FROM drafts d "
                    "JOIN voucher_sources vs ON vs.source_kind='draft' AND vs.source_id=d.id "
                    "JOIN vouchers v ON v.id=vs.voucher_id "
                    "WHERE d.book_id=? AND d.id=? "
                    "ORDER BY v.voucher_date,v.id",
                    (book_id, batch["draft_id"]),
                ).fetchall()
                if batch["draft_id"]
                else []
            )
            vouchers = (
                conn.execute(
                    "SELECT v.id,v.voucher_no,v.voucher_date,v.status,d.id AS draft_id,"
                    "d.status AS draft_status "
                    "FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id "
                    "LEFT JOIN voucher_sources vs ON vs.source_kind='draft' AND vs.source_id=d.id "
                    "LEFT JOIN vouchers v ON v.id=vs.voucher_id "
                    "WHERE s.book_id=? AND s.id IN (" + ",".join("?" for _ in bank_ids) + ") "
                    "ORDER BY s.date,s.id",
                    (book_id, *bank_ids),
                ).fetchall()
                if bank_ids
                else []
            )
            report = conn.execute(
                "SELECT id,status,version,created_at FROM report_runs WHERE book_id=? AND period=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (book_id, str(batch["period"])),
            ).fetchone()
            statement_lines = self._statement_lines(conn, book_id, allocations)

        voucher_items = []
        seen_vouchers: set[str] = set()
        for source_role, rows in (("fee_batch", batch_voucher), ("bank_receipt", vouchers)):
            for row in rows:
                if not row["id"] or row["id"] in seen_vouchers:
                    continue
                seen_vouchers.add(row["id"])
                voucher_items.append(
                    {
                        "source_role": source_role,
                        "draft_id": row["draft_id"],
                        "draft_status": row["draft_status"],
                        "voucher_id": row["id"],
                        "voucher_no": row["voucher_no"],
                        "voucher_date": row["voucher_date"],
                        "status": row["status"],
                    }
                )

        segments = [
            _segment(
                "batch",
                "规费批次",
                [
                    {
                        "id": batch["id"],
                        "period": batch["period"],
                        "status": batch["status"],
                        "version": int(batch["version"]),
                        "total_cents": int(batch["total_cents"]),
                        "draft_id": batch["draft_id"],
                        "source_fingerprint": batch["source_fingerprint"],
                    }
                ],
            ),
            _segment(
                "receivables",
                "驾驶员应收",
                [
                    {
                        "id": row["id"],
                        "driver_name": row["driver_name"],
                        "amount_cents": int(row["amount_cents"]),
                        "status": row["status"],
                    }
                    for row in receivables
                ],
                "批次尚未复核，驾驶员应收还未生成",
            ),
            _segment(
                "allocations",
                "银行核对与核销",
                [
                    {
                        "id": row["id"],
                        "receivable_id": row["receivable_id"],
                        "bank_source_id": row["bank_source_id"],
                        "bank_statement_line_id": row["bank_statement_line_id"],
                        "amount_cents": int(row["amount_cents"]),
                        "payer_name": row["payer_name"],
                    }
                    for row in allocations
                ],
                "还没有登记任何银行到账核销",
            ),
            _segment(
                "statement_lines",
                "银行流水行",
                statement_lines,
                "核销没有绑定银行流水行；需要先在银行导入落库后再核销",
            ),
            _segment(
                "vouchers",
                "正式凭证",
                voucher_items,
                "规费批次与银行来源草稿均尚未记账（或尚未生成）",
            ),
            _segment(
                "report",
                "报表快照",
                [
                    {
                        "id": report["id"],
                        "status": report["status"],
                        "version": int(report["version"]),
                        "created_at": report["created_at"],
                    }
                ]
                if report
                else [],
                "本期还没有报表运行记录",
            ),
        ]
        drafts_pending = [
            {
                "draft_id": row["draft_id"],
                "draft_status": row["draft_status"],
            }
            for row in vouchers
            if not row["id"]
        ]
        return {
            "book_id": book_id,
            "period": str(batch["period"]),
            "batch_id": batch["id"],
            "segments": segments,
            "gaps": [f"{item['label']}：{item['reason']}" for item in segments if item["reason"]],
            "pending_drafts": drafts_pending,
            "readonly": True,
            "note": "只读链路视图：不补凭证、不猜匹配；缺失段须由会计按证据补齐。",
        }

    @staticmethod
    def _statement_lines(conn, book_id: str, allocations) -> list[dict]:
        ids = sorted(
            {row["bank_statement_line_id"] for row in allocations if row["bank_statement_line_id"]}
        )
        if not ids:
            return []
        rows = conn.execute(
            "SELECT id,line_date,description,amount_cents,status FROM bank_statement_lines "
            "WHERE book_id=? AND id IN (" + ",".join("?" for _ in ids) + ") ORDER BY line_date,id",
            (book_id, *ids),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "line_date": row["line_date"],
                "description": row["description"],
                "amount_cents": int(row["amount_cents"]),
                "status": row["status"],
            }
            for row in rows
        ]
