"""FSC04 industry capability contracts.

Contracts describe which fixed processors and source contracts a company may use.
They are data, never executable code: processor names are whitelisted and config
keys that could carry SQL, formulas or commands are rejected before persistence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from pydantic import BaseModel, Field

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError

PROCESSORS = {
    "taxi_fee",
    "advertising_project_revenue",
    "property_service",
    "trade_inventory",
    "hospital_summary",
}
CAPABILITIES = {
    "taxi_fee_import",
    "taxi_bank_allocation",
    "advertising_project_import",
    "advertising_revenue_draft",
    "property_service_billing",
    "trade_inventory_match",
    "hospital_department_summary",
}
FORBIDDEN = {"sql", "formula", "script", "command", "eval", "exec"}


class ContractWrite(BaseModel):
    industry_code: str = Field(min_length=1, max_length=80)
    processor_code: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(min_length=1, max_length=20)
    source_contracts: list[dict] = Field(min_length=1, max_length=20)
    evidence_policy: dict = Field(default_factory=dict)


class ContractConfirm(BaseModel):
    expected_version: int = Field(ge=1)


def _digest(value) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


class FscContractService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate(request: ContractWrite):
        if request.processor_code not in PROCESSORS:
            raise AccountingError("处理器未获批准，不能写入行业契约", 422)
        unknown = sorted(set(request.capabilities) - CAPABILITIES)
        if unknown:
            raise AccountingError("行业能力未登记：" + "、".join(unknown), 422)
        for source in request.source_contracts:
            if not isinstance(source, dict) or not source.get("code"):
                raise AccountingError("来源契约必须包含 code", 422)
            if not isinstance(source.get("fields"), list) or not source["fields"]:
                raise AccountingError("来源契约必须列出字段", 422)
        forbidden = sorted(FORBIDDEN.intersection(_walk_keys(request.model_dump())))
        if forbidden:
            raise AccountingError("行业配置不能包含可执行字段：" + "、".join(forbidden), 422)

    @staticmethod
    def _view(row):
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "industry_code": row["industry_code"],
            "version": int(row["version"]),
            "status": row["status"],
            "processor_code": row["processor_code"],
            "capabilities": json.loads(row["capabilities_json"])
            if isinstance(row["capabilities_json"], str)
            else row["capabilities_json"],
            "source_contracts": json.loads(row["source_contracts_json"])
            if isinstance(row["source_contracts_json"], str)
            else row["source_contracts_json"],
            "evidence_policy": json.loads(row["evidence_policy_json"])
            if isinstance(row["evidence_policy_json"], str)
            else row["evidence_policy_json"],
            "fingerprint": row["fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"],
            "activated_by": row["activated_by"],
            "activated_at": row["activated_at"],
            "superseded_at": row["superseded_at"],
        }

    def _event(self, conn, contract_id, event, actor, detail=None):
        conn.execute(
            "INSERT INTO fsc_industry_contract_events(id,contract_id,event,actor_id,at,detail_json) VALUES(?,?,?,?,?,?)",
            (
                uuid4().hex,
                contract_id,
                event,
                actor.user_id,
                self._now(),
                json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _row(self, conn, actor, contract_id):
        row = conn.execute(
            "SELECT * FROM fsc_industry_contracts WHERE id=? AND firm_id=?",
            (contract_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("行业契约不存在", 404)
        self.access.company(conn, actor, row["company_id"])
        return row

    def create_draft(self, actor, company_id, request: ContractWrite):
        self._validate(request)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            latest = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM fsc_industry_contracts WHERE firm_id=? AND company_id=? AND industry_code=?",
                (actor.firm_id, company_id, request.industry_code),
            ).fetchone()[0]
            version = int(latest) + 1
            conn.execute(
                "UPDATE fsc_industry_contracts SET status='superseded', superseded_at=? "
                "WHERE firm_id=? AND company_id=? AND industry_code=? AND status='draft'",
                (self._now(), actor.firm_id, company_id, request.industry_code),
            )
            contract_id = uuid4().hex
            fingerprint = _digest(
                {**request.model_dump(), "company_id": company_id, "version": version}
            )
            conn.execute(
                "INSERT INTO fsc_industry_contracts(id,firm_id,company_id,industry_code,version,status,"
                "processor_code,capabilities_json,source_contracts_json,evidence_policy_json,fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?,?)",
                (
                    contract_id,
                    actor.firm_id,
                    company_id,
                    request.industry_code,
                    version,
                    request.processor_code,
                    json.dumps(request.capabilities, ensure_ascii=False),
                    json.dumps(request.source_contracts, ensure_ascii=False),
                    json.dumps(request.evidence_policy, ensure_ascii=False),
                    fingerprint,
                    actor.user_id,
                    self._now(),
                ),
            )
            self._event(conn, contract_id, "created", actor, {"version": version})
            return self._view(self._row(conn, actor, contract_id))

    def confirm(self, actor, contract_id, expected_version: int):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, contract_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, contract_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if row["status"] != "draft":
                raise AccountingError("只有草稿行业契约可以确认", 409)
            if int(row["version"]) != expected_version:
                raise AccountingError("行业契约版本已变化，请刷新后重新核对", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("行业契约需要另一名有权限人员独立确认", 409)
            conn.execute(
                "UPDATE fsc_industry_contracts SET status='confirmed',confirmed_by=?,confirmed_at=? WHERE id=? AND status='draft'",
                (actor.user_id, self._now(), contract_id),
            )
            self._event(conn, contract_id, "confirmed", actor)
            return self._view(self._row(conn, actor, contract_id))

    def activate(self, actor, contract_id, expected_version: int):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, contract_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, contract_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if row["status"] != "confirmed":
                raise AccountingError("只有已确认行业契约可以生效", 409)
            if int(row["version"]) != expected_version:
                raise AccountingError("行业契约版本已变化，请刷新后重新核对", 409)
            if self.db.is_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(? )::bigint)",
                    (f"fsc-contract:{row['company_id']}:{row['industry_code']}",),
                )
            profile = conn.execute(
                "SELECT id FROM fsc_company_profiles WHERE company_id=? AND firm_id=? AND status='confirmed' AND industry=? "
                "ORDER BY version DESC LIMIT 1",
                (row["company_id"], actor.firm_id, row["industry_code"]),
            ).fetchone()
            if not profile:
                raise AccountingError("企业行业档案尚未确认，不能启用到生效", 409)
            previous = conn.execute(
                "SELECT id FROM fsc_industry_contracts WHERE firm_id=? AND company_id=? AND industry_code=? AND status='active'",
                (actor.firm_id, row["company_id"], row["industry_code"]),
            ).fetchall()
            for old in previous:
                conn.execute(
                    "UPDATE fsc_industry_contracts SET status='superseded',superseded_at=? WHERE id=?",
                    (self._now(), old["id"]),
                )
                self._event(conn, old["id"], "superseded", actor, {"by": contract_id})
            conn.execute(
                "UPDATE fsc_industry_contracts SET status='active',activated_by=?,activated_at=? WHERE id=?",
                (actor.user_id, self._now(), contract_id),
            )
            self._event(conn, contract_id, "activated", actor)
            return self._view(self._row(conn, actor, contract_id))

    def deactivate(self, actor, contract_id):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, contract_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, contract_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if row["status"] != "active":
                raise AccountingError("只有生效中的行业契约可以停用", 409)
            conn.execute(
                "UPDATE fsc_industry_contracts SET status='inactive' WHERE id=?",
                (contract_id,),
            )
            self._event(conn, contract_id, "deactivated", actor)
            return self._view(self._row(conn, actor, contract_id))

    def list(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            rows = conn.execute(
                "SELECT * FROM fsc_industry_contracts WHERE company_id=? AND firm_id=? ORDER BY industry_code,version DESC",
                (company_id, actor.firm_id),
            ).fetchall()
            return {"items": [self._view(row) for row in rows]}

    def active_matrix(self, actor):
        with self.db.transaction(actor=actor) as conn:
            rows = conn.execute(
                """
                SELECT c.*, co.name AS company_name
                  FROM fsc_industry_contracts c
                  JOIN companies co ON co.id=c.company_id
                 WHERE c.firm_id=? AND c.status='active'
                 ORDER BY co.name,c.industry_code
                """,
                (actor.firm_id,),
            ).fetchall()
            return {
                "items": [{**self._view(row), "company_name": row["company_name"]} for row in rows]
            }

    def capability_matrix(self, actor):
        """Per-company enabled/disabled capability view; no capability is implied by a profile alone."""
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            companies = conn.execute(
                "SELECT id,name FROM companies WHERE id IN ("
                "SELECT company_id FROM books WHERE firm_id=? UNION "
                "SELECT company_id FROM fsc_company_profiles WHERE firm_id=? UNION "
                "SELECT company_id FROM fsc_industry_contracts WHERE firm_id=?) ORDER BY name",
                (actor.firm_id, actor.firm_id, actor.firm_id),
            ).fetchall()
            profiles = conn.execute(
                "SELECT company_id,industry,version,status FROM fsc_company_profiles "
                "WHERE firm_id=? AND status='confirmed' ORDER BY company_id,version DESC",
                (actor.firm_id,),
            ).fetchall()
            contracts = conn.execute(
                "SELECT company_id,industry_code,version,status,processor_code,capabilities_json "
                "FROM fsc_industry_contracts WHERE firm_id=? AND status='active' "
                "ORDER BY company_id,industry_code",
                (actor.firm_id,),
            ).fetchall()
        latest_profile = {}
        for row in profiles:
            latest_profile.setdefault(row["company_id"], row)
        active_contract = {}
        for row in contracts:
            active_contract.setdefault(row["company_id"], row)
        items = []
        for company in companies:
            profile = latest_profile.get(company["id"])
            contract = active_contract.get(company["id"])
            capabilities = []
            if contract:
                raw = contract["capabilities_json"]
                capabilities = json.loads(raw) if isinstance(raw, str) else raw
            enabled = sorted(set(capabilities))
            unsupported = sorted(CAPABILITIES - set(enabled))
            if not profile:
                reason = "企业行业档案未确认"
            elif not contract:
                reason = "尚无已生效行业契约"
            else:
                reason = ""
            items.append(
                {
                    "company_id": company["id"],
                    "company_name": company["name"],
                    "industry": profile["industry"]
                    if profile
                    else (contract["industry_code"] if contract else None),
                    "profile_version": int(profile["version"]) if profile else None,
                    "contract_version": int(contract["version"]) if contract else None,
                    "processor_code": contract["processor_code"] if contract else None,
                    "enabled": enabled,
                    "unsupported": unsupported,
                    "reason": reason,
                }
            )
        return {
            "catalog": sorted(CAPABILITIES),
            "items": items,
            "summary": {
                "companies": len(items),
                "with_confirmed_profile": sum(
                    1 for item in items if item["profile_version"] is not None
                ),
                "with_active_contract": sum(
                    1 for item in items if item["contract_version"] is not None
                ),
            },
        }
