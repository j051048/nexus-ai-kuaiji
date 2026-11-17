"""Versioned repair package contracts and cumulative quota ledger (FSC16)."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.domain import AccountingError
from ..accounting.invariants import validate_period
from ..accounting.models import InputModel
from ..accounting.service import AccountingService, now


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json_list(value):
    return value if isinstance(value, list) else json.loads(value)


def _cents(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AccountingError(f"{name} 必须是正整数分", 422)
    return value


class PackageContractDraft(InputModel):
    contract_no: StrictStr = Field(min_length=1, max_length=120)
    coverage_scope: StrictStr = Field(min_length=3, max_length=500)
    vehicle_external_ids: list[StrictStr] = Field(min_length=1, max_length=200)
    valid_from: StrictStr
    valid_to: StrictStr
    limit_cents: int = Field(gt=0)
    policy: StrictStr = Field(pattern="^(contract_cumulative|monthly_reset)$")
    reason: StrictStr = Field(min_length=3, max_length=500)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _period(cls, value: str) -> str:
        return validate_period(value)


class PackageVersion(InputModel):
    expected_version: int = Field(gt=0)


class PackageConsume(InputModel):
    source_key: StrictStr = Field(min_length=1, max_length=160)
    source_version: int = Field(gt=0)
    period: StrictStr
    amount_cents: int = Field(gt=0)
    vehicle_external_id: StrictStr | None = None
    idempotency_key: StrictStr = Field(min_length=1, max_length=120)

    @field_validator("period")
    @classmethod
    def _period(cls, value: str) -> str:
        return validate_period(value)


class PackageRelease(InputModel):
    amount_cents: int = Field(gt=0)
    reason: StrictStr = Field(min_length=3, max_length=500)
    idempotency_key: StrictStr = Field(min_length=1, max_length=120)


class RepairPackageService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _book(self, conn, actor, book_id, capability="read"):
        return self.accounting.access.book(conn, actor, book_id, capability)

    @staticmethod
    def _contract_view(row):
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "contract_no": row["contract_no"],
            "version": int(row["version"]),
            "status": row["status"],
            "coverage_scope": row["coverage_scope"],
            "vehicle_external_ids": _json_list(row["vehicle_external_ids_json"]),
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "limit_cents": int(row["limit_cents"]),
            "policy": row["policy"],
            "logical_id": row["logical_id"],
            "fingerprint": row["fingerprint"],
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"],
            "activated_at": row["activated_at"],
            "reason": row["reason"],
        }

    @staticmethod
    def _usage_view(row):
        return {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "book_id": row["book_id"],
            "period": row["period"],
            "source_key": row["source_key"],
            "source_version": int(row["source_version"]),
            "amount_cents": int(row["amount_cents"]),
            "kind": row["kind"],
            "release_of": row["release_of"],
            "reason": row["reason"],
            "created_at": row["created_at"],
        }

    def _contract(self, conn, book_id, contract_id):
        row = conn.execute(
            "SELECT * FROM repair_package_contracts WHERE id=? AND book_id=?",
            (contract_id, book_id),
        ).fetchone()
        if not row:
            raise AccountingError("包干合同不存在", 404)
        return row

    def _used_cents(self, conn, contract_id) -> int:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN kind='consume' THEN amount_cents ELSE -amount_cents END),0) AS used "
            "FROM repair_package_usage WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        return int(row["used"] or 0)

    def create_draft(self, actor, book_id, request: PackageContractDraft):
        with self.db.transaction(actor=actor) as conn:
            scope = self._book(conn, actor, book_id, "edit")
            previous = conn.execute(
                "SELECT * FROM repair_package_contracts WHERE book_id=? AND contract_no=? "
                "ORDER BY version DESC LIMIT 1",
                (book_id, request.contract_no),
            ).fetchone()
        if request.valid_to < request.valid_from:
            raise AccountingError("合同结束期间不能早于开始期间", 422)
        vehicles = sorted({str(value).strip() for value in request.vehicle_external_ids if str(value).strip()})
        if not vehicles:
            raise AccountingError("包干合同必须列明适用车辆", 422)
        version = int(previous["version"]) + 1 if previous else 1
        logical_id = previous["logical_id"] if previous else uuid4().hex
        content = {
            "book_id": book_id,
            "contract_no": request.contract_no,
            "version": version,
            "coverage_scope": request.coverage_scope,
            "vehicle_external_ids": vehicles,
            "valid_from": request.valid_from,
            "valid_to": request.valid_to,
            "limit_cents": request.limit_cents,
            "policy": request.policy,
            "reason": request.reason,
        }
        contract_id = uuid4().hex
        stamp = now()
        with self.db.transaction(
            write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
        ) as conn:
            conn.execute(
                "INSERT INTO repair_package_contracts("
                "id,firm_id,company_id,book_id,contract_no,version,status,coverage_scope,"
                "vehicle_external_ids_json,valid_from,valid_to,limit_cents,policy,logical_id,"
                "fingerprint,created_by,created_at,reason) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    contract_id,
                    scope["firm_id"],
                    scope["company_id"],
                    book_id,
                    request.contract_no,
                    version,
                    "draft",
                    request.coverage_scope,
                    json.dumps(vehicles, ensure_ascii=False),
                    request.valid_from,
                    request.valid_to,
                    request.limit_cents,
                    request.policy,
                    logical_id,
                    _fingerprint(content),
                    actor.user_id,
                    stamp,
                    request.reason,
                ),
            )
            row = self._contract(conn, book_id, contract_id)
        return self._contract_view(row)

    def confirm(self, actor, book_id, contract_id, expected_version: int):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id, "review")
            row = self._contract(conn, book_id, contract_id)
        if int(row["version"]) != expected_version:
            raise AccountingError("包干合同版本已变化，请重新加载", 409)
        if row["created_by"] == actor.user_id:
            raise AccountingError("包干合同必须由另一名有复核权限的人员确认", 409)
        if row["status"] != "draft":
            raise AccountingError("只有草稿状态的包干合同可以确认", 409)
        with self.db.transaction(
            write=True, actor=actor, company_id=row["company_id"], write_scope="company"
        ) as conn:
            conn.execute(
                "UPDATE repair_package_contracts SET status='confirmed',confirmed_by=?,confirmed_at=? "
                "WHERE id=? AND book_id=? AND version=? AND status='draft'",
                (actor.user_id, now(), contract_id, book_id, expected_version),
            )
            updated = self._contract(conn, book_id, contract_id)
        return self._contract_view(updated)

    def activate(self, actor, book_id, contract_id, expected_version: int):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id, "edit")
            row = self._contract(conn, book_id, contract_id)
        if int(row["version"]) != expected_version:
            raise AccountingError("包干合同版本已变化，请重新加载", 409)
        if row["status"] != "confirmed":
            raise AccountingError("只有已确认的包干合同可以生效", 409)
        stamp = now()
        with self.db.transaction(
            write=True, actor=actor, company_id=row["company_id"], write_scope="company"
        ) as conn:
            conn.execute(
                "UPDATE repair_package_contracts SET status='superseded',superseded_at=? "
                "WHERE book_id=? AND contract_no=? AND status='active'",
                (stamp, book_id, row["contract_no"]),
            )
            conn.execute(
                "UPDATE repair_package_contracts SET status='active',activated_at=? "
                "WHERE id=? AND book_id=? AND version=? AND status='confirmed'",
                (stamp, contract_id, book_id, expected_version),
            )
            updated = self._contract(conn, book_id, contract_id)
        return self._contract_view(updated)

    def consume(self, actor, book_id, contract_id, request: PackageConsume):
        amount = _cents(request.amount_cents, "包干占用金额")
        with self.db.transaction(actor=actor) as conn:
            scope = self._book(conn, actor, book_id, "edit")
            contract = self._contract(conn, book_id, contract_id)
        if contract["status"] != "active":
            raise AccountingError("包干合同未生效或已停用", 409)
        if request.period < contract["valid_from"] or request.period > contract["valid_to"]:
            raise AccountingError("维修期间不在包干合同有效期内", 409)
        vehicles = _json_list(contract["vehicle_external_ids_json"])
        if request.vehicle_external_id and request.vehicle_external_id not in vehicles:
            raise AccountingError("该车辆不在包干合同覆盖范围内", 409)
        content = {
            "contract_id": contract_id,
            "book_id": book_id,
            "period": request.period,
            "source_key": request.source_key,
            "source_version": request.source_version,
            "amount_cents": amount,
            "vehicle_external_id": request.vehicle_external_id,
            "kind": "consume",
        }
        digest = _fingerprint(content)
        with self.db.transaction(
            write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
        ) as conn:
            existing = conn.execute(
                "SELECT * FROM repair_package_usage WHERE book_id=? AND idempotency_key=?",
                (book_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != digest or existing["kind"] != "consume":
                    raise AccountingError("幂等键已用于不同的包干占用", 409)
                return {**self._usage_view(existing), "replayed": True}
            used = self._used_cents(conn, contract_id)
            if used + amount > int(contract["limit_cents"]):
                raise AccountingError(
                    f"包干额度不足，剩余 {max(0, int(contract['limit_cents']) - used)} 分", 409
                )
            usage_id = uuid4().hex
            conn.execute(
                "INSERT INTO repair_package_usage("
                "id,contract_id,firm_id,company_id,book_id,period,source_key,source_version,"
                "amount_cents,kind,release_of,reason,idempotency_key,fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    usage_id,
                    contract_id,
                    scope["firm_id"],
                    scope["company_id"],
                    book_id,
                    request.period,
                    request.source_key,
                    request.source_version,
                    amount,
                    "consume",
                    None,
                    None,
                    request.idempotency_key,
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM repair_package_usage WHERE id=?", (usage_id,)
            ).fetchone()
        return {**self._usage_view(row), "replayed": False}

    def release(self, actor, book_id, consume_id, request: PackageRelease):
        amount = _cents(request.amount_cents, "释放金额")
        with self.db.transaction(actor=actor) as conn:
            scope = self._book(conn, actor, book_id, "edit")
            consumed = conn.execute(
                "SELECT * FROM repair_package_usage WHERE id=? AND book_id=? AND kind='consume'",
                (consume_id, book_id),
            ).fetchone()
            if not consumed:
                raise AccountingError("包干占用记录不存在", 404)
            contract = self._contract(conn, book_id, consumed["contract_id"])
        digest = _fingerprint(
            {
                "contract_id": contract["id"],
                "book_id": book_id,
                "release_of": consume_id,
                "amount_cents": amount,
                "reason": request.reason,
                "kind": "release",
            }
        )
        with self.db.transaction(
            write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
        ) as conn:
            existing = conn.execute(
                "SELECT * FROM repair_package_usage WHERE book_id=? AND idempotency_key=?",
                (book_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != digest or existing["kind"] != "release":
                    raise AccountingError("幂等键已用于不同的包干释放", 409)
                return {**self._usage_view(existing), "replayed": True}
            released = int(
                conn.execute(
                    "SELECT COALESCE(SUM(amount_cents),0) AS total FROM repair_package_usage "
                    "WHERE release_of=? AND kind='release'",
                    (consume_id,),
                ).fetchone()["total"]
                or 0
            )
            if released + amount > int(consumed["amount_cents"]):
                raise AccountingError("释放金额不能超过原占用金额", 409)
            usage_id = uuid4().hex
            conn.execute(
                "INSERT INTO repair_package_usage("
                "id,contract_id,firm_id,company_id,book_id,period,source_key,source_version,"
                "amount_cents,kind,release_of,reason,idempotency_key,fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    usage_id,
                    contract["id"],
                    scope["firm_id"],
                    scope["company_id"],
                    book_id,
                    consumed["period"],
                    consumed["source_key"],
                    consumed["source_version"],
                    amount,
                    "release",
                    consume_id,
                    request.reason,
                    request.idempotency_key,
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM repair_package_usage WHERE id=?", (usage_id,)
            ).fetchone()
        return {**self._usage_view(row), "replayed": False}

    def list_contracts(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM repair_package_contracts WHERE book_id=? ORDER BY contract_no,version",
                (book_id,),
            ).fetchall()
        return {"items": [self._contract_view(row) for row in rows]}

    def summary(self, actor, book_id, contract_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            contract = self._contract(conn, book_id, contract_id)
            rows = conn.execute(
                "SELECT * FROM repair_package_usage WHERE contract_id=? ORDER BY created_at,id",
                (contract_id,),
            ).fetchall()
        consumed = sum(int(row["amount_cents"]) for row in rows if row["kind"] == "consume")
        released = sum(int(row["amount_cents"]) for row in rows if row["kind"] == "release")
        used = consumed - released
        return {
            "contract": self._contract_view(contract),
            "limit_cents": int(contract["limit_cents"]),
            "consumed_cents": used,
            "remaining_cents": max(0, int(contract["limit_cents"]) - used),
            "usage": [self._usage_view(row) for row in rows],
        }
