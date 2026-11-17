"""Tax-basis evidence review for TAX14.

The review stores the explicit policy, base, adjustments and the manual
evidence used for an exception. It does not turn an unknown amount into zero,
and it does not claim that a client filing or payment was independently
confirmed.
"""

import hashlib
import json
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.domain import AccountingError
from ..accounting.invariants import (
    money_to_cents,
    scale_by_basis_points,
    validate_period,
)
from ..accounting.models import InputModel
from ..accounting.service import AccountingService, now


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _signed_cents(value):
    text = str(value).strip()
    if text.startswith("-"):
        return -money_to_cents(text[1:])
    return money_to_cents(text)


class TaxBasisPolicy(InputModel):
    version: StrictStr = Field(min_length=1, max_length=80)
    taxpayer_type: StrictStr
    rate_bp: int = Field(strict=True, ge=0, le=10000)
    surtax_bp: int = Field(default=0, strict=True, ge=0, le=10000)
    effective_from: StrictStr
    authority: StrictStr = Field(min_length=3, max_length=500)
    source_ref: StrictStr = Field(min_length=1, max_length=500)

    @field_validator("taxpayer_type")
    @classmethod
    def valid_type(cls, value):
        if value not in {"small_scale", "general"}:
            raise ValueError("纳税人类型只能是 small_scale 或 general")
        return value

    @field_validator("effective_from")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class TaxBasisAdjustment(InputModel):
    code: StrictStr = Field(min_length=1, max_length=80)
    amount: StrictStr
    reason: StrictStr = Field(min_length=3, max_length=500)


class TaxBasisGenerate(InputModel):
    period: StrictStr
    policy: TaxBasisPolicy
    base: StrictStr
    adjustments: list[TaxBasisAdjustment] = Field(default_factory=list, max_length=100)
    manual: bool = False
    manual_evidence: StrictStr = Field(default="", max_length=1000)
    source_refs: list[StrictStr] = Field(default_factory=list, max_length=20)
    workpaper_id: StrictStr = Field(default="", max_length=100)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class TaxBasisReview(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class TaxBasisService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _book(self, conn, actor, book_id, write=False):
        return self.accounting.access.book(conn, actor, book_id, "review" if write else "read")

    @staticmethod
    def _payload(period, policy, base, adjustments):
        return {
            "period": period,
            "policy": policy.model_dump(),
            "base_cents": base,
            "adjustments": [item.model_dump() for item in adjustments],
        }

    def generate(self, actor, book_id, request: TaxBasisGenerate):
        if request.policy.effective_from > request.period:
            raise AccountingError("计税政策尚未在所选期间生效", 422)
        if (
            request.policy.taxpayer_type == "general"
            and request.manual
            and not request.manual_evidence.strip()
        ):
            raise AccountingError("一般纳税人人工核定必须保留依据原件或说明", 422)
        if request.manual and not request.manual_evidence.strip():
            raise AccountingError("人工核定必须填写证据说明，不能把未知金额当作零", 422)
        if not request.source_refs and not request.manual:
            raise AccountingError("必须提供政策或来源依据", 422)
        base = money_to_cents(request.base)
        if base < 0:
            raise AccountingError("计税基数不能为负数", 422)
        adjustment_total = 0
        for item in request.adjustments:
            amount = _signed_cents(item.amount)
            adjustment_total += amount
        tax = scale_by_basis_points(base, request.policy.rate_bp)
        tax += scale_by_basis_points(tax, request.policy.surtax_bp)
        tax += adjustment_total
        with self.db.transaction(actor=actor) as conn:
            company_id = self._book(conn, actor, book_id, write=True)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, write=True)
            self.accounting._open(book)
            if request.period != book["period"]:
                raise AccountingError("计税期间与账套期间不一致", 422)
            if request.workpaper_id:
                workpaper = conn.execute(
                    "SELECT * FROM tax_workpapers WHERE id=? AND book_id=? AND period=?",
                    (request.workpaper_id, book_id, request.period),
                ).fetchone()
                if not workpaper:
                    raise AccountingError("关联底稿不存在、无权访问或期间不一致", 404)
            version = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM tax_basis_reviews WHERE book_id=? AND period=?",
                    (book_id, request.period),
                ).fetchone()[0]
            )
            payload = self._payload(request.period, request.policy, base, request.adjustments)
            fp = _fingerprint(payload)
            review_id = uuid4().hex
            conn.execute(
                "INSERT INTO tax_basis_reviews(id,firm_id,company_id,book_id,period,version,status,"
                "workpaper_id,policy_json,policy_fingerprint,base_cents,adjustments_json,manual_evidence,"
                "source_refs_json,tax_cents,source_fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?)",
                (
                    review_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    request.period,
                    version,
                    request.workpaper_id or None,
                    json.dumps(request.policy.model_dump(), ensure_ascii=False, sort_keys=True),
                    _fingerprint(request.policy.model_dump()),
                    base,
                    json.dumps(
                        [item.model_dump() for item in request.adjustments],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    request.manual_evidence.strip(),
                    json.dumps(request.source_refs, ensure_ascii=False),
                    tax,
                    fp,
                    actor.user_id,
                    now(),
                ),
            )
            return self._view(
                conn,
                conn.execute("SELECT * FROM tax_basis_reviews WHERE id=?", (review_id,)).fetchone(),
            )

    def list(self, actor, book_id, period=None):
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            sql = "SELECT * FROM tax_basis_reviews WHERE book_id=?"
            params = [book_id]
            if period:
                sql += " AND period=?"
                params.append(validate_period(period))
            sql += " ORDER BY period DESC,version DESC"
            return {
                "items": [self._view(conn, row) for row in conn.execute(sql, params).fetchall()]
            }

    def get(self, actor, book_id, review_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM tax_basis_reviews WHERE id=? AND book_id=?", (review_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("计税核定不存在或无权访问", 404)
            return self._view(conn, row)

    def review(self, actor, book_id, review_id, request: TaxBasisReview):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._book(conn, actor, book_id, write=True)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._book(conn, actor, book_id, write=True)
            row = conn.execute(
                "SELECT * FROM tax_basis_reviews WHERE id=? AND book_id=?", (review_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("计税核定不存在或无权访问", 404)
            if int(row["version"]) != request.expected_version:
                raise AccountingError("计税核定版本已变化", 409)
            if row["status"] != "draft":
                raise AccountingError("只有待复核的计税核定可以复核", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("制单人不能复核自己提交的计税核定", 403)
            conn.execute(
                "UPDATE tax_basis_reviews SET status='reviewed',reviewed_by=?,reviewed_at=? WHERE id=?",
                (actor.user_id, now(), review_id),
            )
            return self._view(
                conn,
                conn.execute("SELECT * FROM tax_basis_reviews WHERE id=?", (review_id,)).fetchone(),
            )

    def _view(self, conn, row):
        workpaper = None
        if row["workpaper_id"]:
            workpaper = conn.execute(
                "SELECT id,version,status,tax_cents,variance_cents FROM tax_workpapers WHERE id=?",
                (row["workpaper_id"],),
            ).fetchone()
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "period": row["period"],
            "version": int(row["version"]),
            "status": row["status"],
            "policy": json.loads(row["policy_json"]),
            "policy_fingerprint": row["policy_fingerprint"],
            "base_cents": int(row["base_cents"]),
            "adjustments": json.loads(row["adjustments_json"]),
            "manual_evidence": row["manual_evidence"],
            "source_refs": json.loads(row["source_refs_json"]),
            "tax_cents": int(row["tax_cents"]),
            "source_fingerprint": row["source_fingerprint"],
            "workpaper": dict(workpaper) if workpaper else None,
            "created_by": row["created_by"],
            "reviewed_by": row["reviewed_by"],
        }
