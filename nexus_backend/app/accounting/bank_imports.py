"""Stable statement identities and explicit review of similar bank records."""

import json
from uuid import uuid4
from pydantic import Field, StrictStr
from .access import AccessControl
from .domain import AccountingError
from .models import InputModel, cents_to_money, money_to_cents
from .service import fingerprint, now


class DuplicateDistinction(InputModel):
    source_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    other_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


DUPLICATE_SQL = """SELECT o.*,COALESCE(ko.original_external_id,o.external_id) AS original_external_id,
    COALESCE(ko.stable_transaction_id,o.external_id) AS stable_transaction_id,
    od.status AS draft_status,od.version AS draft_version,
    CASE WHEN decision.source_id IS NULL THEN 0 ELSE 1 END AS distinguished
    FROM sources s JOIN sources o ON o.book_id=s.book_id AND o.id<>s.id AND o.source_kind='bank'
    LEFT JOIN bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id
    LEFT JOIN bank_import_keys ko ON ko.source_id=o.id AND ko.book_id=o.book_id
    LEFT JOIN drafts od ON od.source_id=o.id AND od.book_id=o.book_id
    LEFT JOIN bank_duplicate_decisions decision ON decision.book_id=s.book_id
      AND decision.source_id=CASE WHEN s.id<o.id THEN s.id ELSE o.id END
      AND decision.other_source_id=CASE WHEN s.id<o.id THEN o.id ELSE s.id END
    WHERE s.id=? AND s.book_id=? AND s.source_kind='bank' AND s.date=o.date AND s.direction=o.direction
      AND s.amount_cents=o.amount_cents AND s.counterparty=o.counterparty
      AND (k.source_id IS NOT NULL OR ko.source_id IS NOT NULL)
      AND (k.source_id IS NULL OR ko.source_id IS NULL OR k.bank_account=ko.bank_account)"""
DUPLICATE_SQL += " AND NOT EXISTS(SELECT 1 FROM bank_source_dispositions x WHERE x.book_id=s.book_id AND x.source_id IN (s.id,o.id))"


class BankImportService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    def import_in_transaction(self, conn, actor, book, request):
        imported = duplicates = 0
        for row in request.rows:
            if row.date[:7] != book["period"]:
                raise AccountingError(f"流水 {row.external_id} 日期不属于当前会计期间")
            stable_id = row.stable_transaction_id or row.external_id
            canonical = row.model_dump(exclude={"external_id", "stable_transaction_id"})
            digest = fingerprint(canonical)
            existing = conn.execute(
                "SELECT content_fingerprint FROM bank_import_keys WHERE book_id=? AND namespace=? AND bank_account=? AND stable_transaction_id=?",
                (book["id"], request.namespace, request.bank_account, stable_id),
            ).fetchone()
            if existing:
                if existing["content_fingerprint"] != digest:
                    raise AccountingError(
                        f"稳定交易编号 {stable_id} 已存在且内容不同，整批导入已回滚", 409
                    )
                duplicates += 1
                continue
            if self.db.is_postgres:
                elsewhere = conn.execute(
                    "SELECT accounting.bank_identity_elsewhere(?,?,?,?)",
                    (book["id"], request.namespace, request.bank_account, stable_id),
                ).fetchone()[0]
            else:
                elsewhere = (
                    conn.execute(
                        "SELECT 1 FROM bank_import_keys WHERE firm_id=? AND company_id=? AND book_id<>? AND namespace=? AND bank_account=? AND stable_transaction_id=?",
                        (
                            actor.firm_id,
                            book["company_id"],
                            book["id"],
                            request.namespace,
                            request.bank_account,
                            stable_id,
                        ),
                    ).fetchone()
                    is not None
                )
            if elsewhere:
                # Expose neither an inaccessible book identity nor its content.
                raise AccountingError(
                    "该企业同一银行交易已有记录，请联系会计核对账套，整批导入已回滚", 409
                )
            source_id = uuid4().hex
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    book["id"],
                    "bank:v1:" + source_id,
                    request.source_name,
                    row.date,
                    row.description,
                    row.counterparty,
                    money_to_cents(row.amount),
                    row.direction,
                    row.evidence_ref,
                    digest,
                ),
            )
            conn.execute(
                "INSERT INTO bank_import_keys(source_id,book_id,firm_id,company_id,namespace,bank_account,stable_transaction_id,original_external_id,content_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    book["id"],
                    actor.firm_id,
                    book["company_id"],
                    request.namespace,
                    request.bank_account,
                    stable_id,
                    row.external_id,
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            imported += 1
        return {"imported": imported, "duplicates": duplicates}

    def blocking_issues_in_transaction(self, conn, actor, source):
        if source["source_kind"] != "bank":
            return []
        if self.db.is_postgres:
            blocked = conn.execute(
                "SELECT accounting.bank_source_import_blocked(?,?)",
                (source["book_id"], source["id"]),
            ).fetchone()[0]
        else:
            blocked = (
                conn.execute(
                    "SELECT 1 FROM bank_source_dispositions WHERE book_id=? AND source_id=?",
                    (source["book_id"], source["id"]),
                ).fetchone()
                is not None
                or conn.execute(
                    DUPLICATE_SQL + " AND decision.source_id IS NULL LIMIT 1",
                    (source["id"], source["book_id"]),
                ).fetchone()
                is not None
            )
        return (
            [
                "同账套存在日期、金额、方向和交易对方相同的其他银行流水，须会计核对账号及是否为独立交易"
            ]
            if blocked
            else []
        )

    def overview(self, actor, book_id, source_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id)
            source = conn.execute(
                "SELECT * FROM sources WHERE id=? AND book_id=?", (source_id, book_id)
            ).fetchone()
            if not source:
                raise AccountingError("来源不存在或无权访问", 404)
            identity = conn.execute(
                "SELECT namespace,bank_account,stable_transaction_id,original_external_id FROM bank_import_keys WHERE source_id=? AND book_id=?",
                (source_id, book_id),
            ).fetchone()
            # Bounded detail: aggregate first; approval checks every pair, including
            # those beyond the UI page, rather than accepting a truncated result.
            total = conn.execute(
                "SELECT COUNT(*) FROM (" + DUPLICATE_SQL + ") candidates", (source_id, book_id)
            ).fetchone()[0]
            rows = conn.execute(
                DUPLICATE_SQL + " ORDER BY distinguished,o.id LIMIT 50", (source_id, book_id)
            ).fetchall()
            return {
                "source_id": source_id,
                "fingerprint": source["fingerprint"],
                "identity": dict(identity) if identity else None,
                "total": total,
                "has_more": total > len(rows),
                "permissions": {
                    "review": book["permissions"]["review"] and book["period_status"] == "open"
                },
                "candidates": [
                    {
                        "id": r["id"],
                        "external_id": r["original_external_id"],
                        "stable_transaction_id": r["stable_transaction_id"],
                        "date": r["date"],
                        "description": r["description"],
                        "amount": cents_to_money(r["amount_cents"]),
                        "fingerprint": r["fingerprint"],
                        "status": r["draft_status"] or "unprocessed",
                        "distinguished": bool(r["distinguished"]),
                    }
                    for r in rows
                ],
            }

    def distinguish(self, actor, book_id, source_id, other_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            low, high = sorted((source_id, other_id))
            source = conn.execute(
                "SELECT * FROM sources WHERE id=? AND book_id=?", (source_id, book_id)
            ).fetchone()
            other = conn.execute(
                DUPLICATE_SQL + " AND o.id=?", (source_id, book_id, other_id)
            ).fetchone()
            if not source or not other:
                raise AccountingError("相似流水不存在或无权访问", 404)
            if (
                request.source_fingerprint != source["fingerprint"]
                or request.other_fingerprint != other["fingerprint"]
            ):
                raise AccountingError("流水事实已变化，请重新核对", 409)
            previous = conn.execute(
                "SELECT * FROM bank_duplicate_decisions WHERE source_id=? AND other_source_id=? AND book_id=?",
                (low, high, book_id),
            ).fetchone()
            if previous:
                if previous["reason"] != request.reason:
                    raise AccountingError("该对流水已经核验，不能覆盖历史说明", 409)
                return {"distinct": True, "duplicate": True}
            if book["period_status"] != "open":
                raise AccountingError("该会计期间已关闭，不可核验", 403)
            draft = conn.execute(
                "SELECT status,version FROM drafts WHERE source_id=? AND book_id=?",
                (source_id, book_id),
            ).fetchone()
            if (
                not draft
                or draft["status"] != "draft"
                or draft["version"] != request.expected_version
            ):
                raise AccountingError("草稿已变化或冻结，请重新核对", 409)
            fingerprints = {source_id: source["fingerprint"], other_id: other["fingerprint"]}
            conn.execute(
                "INSERT INTO bank_duplicate_decisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    low,
                    high,
                    book_id,
                    actor.firm_id,
                    fingerprints[low],
                    fingerprints[high],
                    request.expected_version,
                    request.reason,
                    actor.user_id,
                    now(),
                ),
            )
            # Both drafts' review context changes. Leave approved/exported peers frozen.
            conn.execute(
                "UPDATE drafts SET version=version+1 WHERE book_id=? AND source_id IN (?,?) AND status='draft'",
                (book_id, source_id, other_id),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    book_id,
                    actor.user_id,
                    "bank.duplicate.distinguish",
                    source_id,
                    json.dumps(
                        {"other_id": other_id, "reason": request.reason}, ensure_ascii=False
                    ),
                    now(),
                ),
            )
            return {"distinct": True, "duplicate": False}
