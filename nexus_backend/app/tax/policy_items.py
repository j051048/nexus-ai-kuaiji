"""OPT07-E policy-driven tax items.

Items live under the existing ``tax_profiles`` version, so a workpaper can
freeze the profile id and its item fingerprints.  This module only calculates
amounts; it never invents a tax rate or an approval.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from pydantic import BaseModel, Field

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.invariants import scale_by_basis_points
from ..accounting.service import now
from uuid import uuid4

KINDS = {
    "vat_output",
    "surtax",
    "culture_fee",
    "agriculture_deduction",
    "cit_prepayment",
    "stamp_duty",
}


class TaxPolicyItemWrite(BaseModel):
    kind: str
    code: str
    name: str
    basis_kind: str
    rate_bp: int = Field(ge=0, le=100_000)
    effective_from: str
    effective_to: str | None = None
    conditions: dict = Field(default_factory=dict)
    authority: str
    approved_by: str


def _fingerprint(item: dict) -> str:
    return hashlib.sha256(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class TaxPolicyItemService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    @staticmethod
    def _period(value: str) -> str:
        try:
            date.fromisoformat(f"{value}-01")
        except (TypeError, ValueError):
            raise AccountingError("税费政策生效期间必须为 YYYY-MM", 422)
        return value

    @staticmethod
    def _validate(item: TaxPolicyItemWrite) -> dict:
        if item.kind not in KINDS:
            raise AccountingError(f"未知税费种类：{item.kind}", 422)
        if not item.code.strip() or not item.name.strip():
            raise AccountingError("税费条目必须填写代码和名称", 422)
        if not item.basis_kind.strip():
            raise AccountingError("税费条目必须声明计税基础来源", 422)
        if len(item.authority.strip()) < 3 or not item.approved_by.strip():
            raise AccountingError("税费条目必须记录依据说明与批准人", 422)
        start = TaxPolicyItemService._period(item.effective_from)
        end = TaxPolicyItemService._period(item.effective_to) if item.effective_to else None
        if end and end < start:
            raise AccountingError("税费条目失效期间不能早于生效期间", 422)
        return {
            "kind": item.kind,
            "code": item.code.strip(),
            "name": item.name.strip(),
            "basis_kind": item.basis_kind.strip(),
            "rate_bp": int(item.rate_bp),
            "effective_from": start,
            "effective_to": end,
            "conditions": item.conditions,
            "authority": item.authority.strip(),
            "approved_by": item.approved_by.strip(),
        }

    def list(self, actor, book_id: str, period: str | None = None):
        period = self._period(period) if period else None
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            profile = conn.execute(
                "SELECT * FROM tax_profiles WHERE company_id=? AND effective_from<=? "
                "ORDER BY effective_from DESC LIMIT 1",
                (book["company_id"], period or book["period"]),
            ).fetchone()
            if not profile:
                return {"profile_id": None, "items": []}
            rows = conn.execute(
                "SELECT * FROM tax_policy_items WHERE profile_id=? ORDER BY kind,code,effective_from",
                (profile["id"],),
            ).fetchall()
        return {"profile_id": profile["id"], "items": [self._view(row) for row in rows]}

    def add(self, actor, book_id: str, items: list[TaxPolicyItemWrite]):
        if not items:
            raise AccountingError("至少登记一条税费政策条目", 422)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "manage")
            profile = conn.execute(
                "SELECT * FROM tax_profiles WHERE company_id=? AND effective_from<=? "
                "ORDER BY effective_from DESC LIMIT 1",
                (book["company_id"], book["period"]),
            ).fetchone()
            if not profile:
                raise AccountingError("请先登记本期间的税务档案，再补充税费条目", 409)
        created = []
        with self.db.transaction(
            write=True,
            actor=actor,
            company_id=book["company_id"],
            write_scope="company",
        ) as conn:
            book = self.access.book(conn, actor, book_id, "manage")
            for raw in items:
                item = self._validate(raw)
                payload = {
                    "profile_id": profile["id"],
                    "firm_id": actor.firm_id,
                    "company_id": book["company_id"],
                    **item,
                }
                fp = _fingerprint(payload)
                if conn.execute(
                    "SELECT 1 FROM tax_policy_items WHERE profile_id=? AND kind=? AND code=? "
                    "AND effective_from=?",
                    (profile["id"], item["kind"], item["code"], item["effective_from"]),
                ).fetchone():
                    raise AccountingError("同一档案下税费条目版本已存在", 409)
                item_id = str(uuid4())
                conn.execute(
                    "INSERT INTO tax_policy_items(id,profile_id,firm_id,company_id,kind,code,name,"
                    "basis_kind,rate_bp,effective_from,effective_to,conditions_json,authority,"
                    "approved_by,created_by,created_at,fingerprint) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        profile["id"],
                        actor.firm_id,
                        book["company_id"],
                        item["kind"],
                        item["code"],
                        item["name"],
                        item["basis_kind"],
                        item["rate_bp"],
                        item["effective_from"],
                        item["effective_to"],
                        json.dumps(item["conditions"], ensure_ascii=False, sort_keys=True),
                        item["authority"],
                        item["approved_by"],
                        actor.user_id,
                        now(),
                        fp,
                    ),
                )
                created.append({"id": item_id, **item, "fingerprint": fp})
        return {"profile_id": profile["id"], "items": created}

    def calculate(self, actor, book_id: str, period: str, bases: dict[str, int]):
        if not isinstance(bases, dict) or not bases:
            raise AccountingError("必须提供计税基础", 422)
        period = self._period(period)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            profile = conn.execute(
                "SELECT * FROM tax_profiles WHERE company_id=? AND effective_from<=? "
                "ORDER BY effective_from DESC LIMIT 1",
                (book["company_id"], period),
            ).fetchone()
            if not profile:
                raise AccountingError(f"缺少{period}生效的税务档案", 409)
            rows = conn.execute(
                "SELECT * FROM tax_policy_items WHERE profile_id=? "
                "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
                (profile["id"], period, period),
            ).fetchall()
        by_basis: dict[str, list] = {}
        for row in rows:
            by_basis.setdefault(row["basis_kind"], []).append(row)
        lines, missing = [], []
        for basis, raw_base in bases.items():
            if type(raw_base) is not int or raw_base < 0:
                raise AccountingError("计税基础必须为非负整数分", 422)
            matched = by_basis.get(basis, [])
            if raw_base and not matched:
                missing.append(basis)
                continue
            for row in matched:
                amount = scale_by_basis_points(raw_base, int(row["rate_bp"]))
                lines.append(
                    {
                        "kind": row["kind"],
                        "code": row["code"],
                        "name": row["name"],
                        "basis_kind": basis,
                        "rate_bp": int(row["rate_bp"]),
                        "base_cents": raw_base,
                        "amount_cents": amount,
                        "item_fingerprint": row["fingerprint"],
                    }
                )
        if missing:
            raise AccountingError("缺少税费政策条目：" + "、".join(sorted(missing)), 409)
        return {
            "period": period,
            "profile_id": profile["id"],
            "lines": lines,
            "total_cents": sum(line["amount_cents"] for line in lines),
        }

    @staticmethod
    def _view(row) -> dict:
        return {
            "id": row["id"],
            "profile_id": row["profile_id"],
            "kind": row["kind"],
            "code": row["code"],
            "name": row["name"],
            "basis_kind": row["basis_kind"],
            "rate_bp": int(row["rate_bp"]),
            "effective_from": row["effective_from"],
            "effective_to": row["effective_to"],
            "conditions": json.loads(row["conditions_json"]),
            "authority": row["authority"],
            "approved_by": row["approved_by"],
            "fingerprint": row["fingerprint"],
        }
