"""FSC04 second domain: advertising project revenue drafts.

This consumes a confirmed shared import envelope; it never posts journals or
creates a receivable. A draft is a reviewable projection bound to the envelope,
contract and row evidence, so changing any of them requires a new run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from pydantic import BaseModel, Field

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError


class AdvertisingDraftWrite(BaseModel):
    envelope_id: str = Field(min_length=1, max_length=80)


def _digest(value) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _rows(value):
    return json.loads(value) if isinstance(value, str) else value


class AdvertisingRevenueService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _view(row, envelope_status="confirmed", contract_status="active"):
        stale = envelope_status != "confirmed" or contract_status != "active"
        if envelope_status != "confirmed":
            reason = "导入信封已被新版本替代或失效"
        elif contract_status != "active":
            reason = "广告收入契约已停用或被替代"
        else:
            reason = ""
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "book_id": row["book_id"],
            "envelope_id": row["envelope_id"],
            "contract_id": row["contract_id"],
            "period": row["period"],
            "source_version": int(row["source_version"]),
            "total_cents": int(row["total_cents"]),
            "lines": _rows(row["lines_json"]),
            "fingerprint": row["fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "stale": stale,
            "stale_reason": reason,
        }

    def generate(self, actor, book_id, request: AdvertisingDraftWrite):
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, capability="review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, capability="review")
            envelope = conn.execute(
                "SELECT * FROM fsc_import_envelopes WHERE id=? AND book_id=? AND firm_id=?",
                (request.envelope_id, book_id, actor.firm_id),
            ).fetchone()
            if not envelope:
                raise AccountingError("导入信封不存在或无权访问", 404)
            if envelope["status"] != "confirmed":
                raise AccountingError("导入信封尚未确认，不能生成领域输出", 409)
            contract = conn.execute(
                "SELECT * FROM fsc_industry_contracts WHERE company_id=? AND firm_id=? "
                "AND status='active' AND processor_code='advertising_project_revenue' "
                "ORDER BY version DESC LIMIT 1",
                (book["company_id"], actor.firm_id),
            ).fetchone()
            if not contract:
                raise AccountingError("没有已生效的广告项目收入处理器契约", 409)
            adopted = [
                row for row in _rows(envelope["rows_json"]) if row.get("status") == "adopted"
            ]
            if not adopted:
                raise AccountingError("导入信封没有可生成的采用行", 422)
            projects = {}
            for row in adopted:
                if row.get("target_kind") != "project" or not row.get("target_id"):
                    raise AccountingError("广告项目收入行必须绑定 project 目标", 422)
                amount = int(row.get("amount_cents") or 0)
                if amount < 0:
                    raise AccountingError("广告项目收入行金额不能为负", 422)
                item = projects.setdefault(
                    row["target_id"],
                    {"project_id": row["target_id"], "amount_cents": 0, "row_numbers": []},
                )
                item["amount_cents"] += amount
                item["row_numbers"].append(int(row["row_number"]))
            lines = []
            for item in sorted(projects.values(), key=lambda value: value["project_id"]):
                item["row_numbers"].sort()
                lines.append(item)
            total = sum(item["amount_cents"] for item in lines)
            existing = conn.execute(
                "SELECT * FROM fsc_advertising_revenue_drafts WHERE envelope_id=? AND contract_id=?",
                (envelope["id"], contract["id"]),
            ).fetchone()
            if existing:
                return {**self._view(existing), "replayed": True}
            fingerprint = _digest(
                {
                    "envelope": envelope["fingerprint"],
                    "contract": contract["fingerprint"],
                    "source_version": int(envelope["source_version"]),
                    "lines": lines,
                }
            )
            draft_id = uuid4().hex
            conn.execute(
                "INSERT INTO fsc_advertising_revenue_drafts(id,firm_id,company_id,book_id,envelope_id,contract_id,"
                "period,source_version,total_cents,lines_json,fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    envelope["id"],
                    contract["id"],
                    str(envelope["period"]),
                    int(envelope["source_version"]),
                    total,
                    json.dumps(lines, ensure_ascii=False),
                    fingerprint,
                    actor.user_id,
                    self._now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM fsc_advertising_revenue_drafts WHERE id=?", (draft_id,)
            ).fetchone()
            return {**self._view(row), "replayed": False}

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, capability="read")
            rows = conn.execute(
                "SELECT d.*,e.status AS envelope_status,c.status AS contract_status "
                "FROM fsc_advertising_revenue_drafts d "
                "LEFT JOIN fsc_import_envelopes e ON e.id=d.envelope_id "
                "LEFT JOIN fsc_industry_contracts c ON c.id=d.contract_id "
                "WHERE d.book_id=? AND d.firm_id=? ORDER BY d.created_at DESC,d.id DESC LIMIT 50",
                (book_id, actor.firm_id),
            ).fetchall()
            return {
                "items": [
                    self._view(
                        row,
                        row["envelope_status"] or "missing",
                        row["contract_status"] or "missing",
                    )
                    for row in rows
                ]
            }
