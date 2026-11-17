"""FSC01 versioned company operating profiles.

A profile is not a set of default switches.  Every required section is either
confirmed or explicitly not applicable, with an owner, evidence, timestamp and
the immutable profile version.  Unknown values stay unknown and block activation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError


SECTIONS = {
    "entity": "主体与集团关系",
    "accounting_authority": "账务权威系统",
    "accounting_standard": "会计制度与科目体系",
    "period_control": "会计期间与关账责任",
    "tax_boundary": "税务登记与申报边界",
    "source_systems": "来源系统与文件接口",
    "document_volume": "月度单据量与峰值",
    "contracts_originals": "合同与原件保管",
    "roles": "岗位职责与授权",
    "sensitive_data": "敏感数据与可见范围",
    "deployment": "部署方式与网络边界",
    "retention": "留存期限与归档",
}
REQUIRED = tuple(SECTIONS)


class ProfileWrite(BaseModel):
    industry: str = Field(min_length=1, max_length=80)
    payload: dict


class ProfileConfirm(BaseModel):
    expected_version: int = Field(ge=1)


class FscProfileService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    @staticmethod
    def _fingerprint(industry, payload, confirmations, version):
        raw = json.dumps(
            {
                "industry": industry,
                "payload": payload,
                "confirmations": confirmations,
                "version": version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _view(row):
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "version": int(row["version"]),
            "status": row["status"],
            "industry": row["industry"],
            "payload": json.loads(row["payload_json"])
            if isinstance(row["payload_json"], str)
            else row["payload_json"],
            "confirmations": json.loads(row["confirmations_json"])
            if isinstance(row["confirmations_json"], str)
            else row["confirmations_json"],
            "fingerprint": row["fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"],
            "sections": list(SECTIONS),
        }

    def _row(self, conn, actor, profile_id):
        row = conn.execute(
            "SELECT * FROM fsc_company_profiles WHERE id=? AND firm_id=?",
            (profile_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("行业档案不存在", 404)
        self.access.company(conn, actor, row["company_id"])
        return row

    def create_draft(self, actor, company_id, request: ProfileWrite):
        with self.db.transaction(
            actor=actor, write=True, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            payload = request.payload
            missing = [key for key in REQUIRED if key not in payload]
            if missing:
                raise AccountingError(
                    "档案缺少栏目：" + "、".join(SECTIONS[key] for key in missing), 422
                )
            latest = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM fsc_company_profiles WHERE firm_id=? AND company_id=?",
                (actor.firm_id, company_id),
            ).fetchone()[0]
            version = int(latest) + 1
            conn.execute(
                "UPDATE fsc_company_profiles SET status='superseded' WHERE firm_id=? AND company_id=? AND status='draft'",
                (actor.firm_id, company_id),
            )
            profile_id = uuid4().hex
            fingerprint = self._fingerprint(request.industry, payload, {}, version)
            conn.execute(
                "INSERT INTO fsc_company_profiles(id,firm_id,company_id,version,status,industry,"
                "payload_json,confirmations_json,fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?, 'draft', ?, ?, '{}', ?, ?, ?)",
                (
                    profile_id,
                    actor.firm_id,
                    company_id,
                    version,
                    request.industry,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    fingerprint,
                    actor.user_id,
                    datetime.now().astimezone().isoformat(),
                ),
            )
            return self._view(self._row(conn, actor, profile_id))

    def confirm(self, actor, profile_id, request: ProfileConfirm):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, profile_id)["company_id"]
        with self.db.transaction(
            actor=actor, write=True, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, profile_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if row["status"] != "draft":
                raise AccountingError("只有草稿档案可以确认", 409)
            if int(row["version"]) != request.expected_version:
                raise AccountingError("档案版本已变化，请刷新后重新确认", 409)
            raw_payload = row["payload_json"]
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            confirmations = {}
            unresolved = []
            for key in REQUIRED:
                item = (
                    payload.get("_confirmations", {}).get(key)
                    if isinstance(payload, dict)
                    else None
                )
                if not item:
                    unresolved.append(SECTIONS[key])
                    continue
                status = item.get("status")
                if status not in {"confirmed", "not_applicable"}:
                    unresolved.append(SECTIONS[key])
                    continue
                if (
                    not str(item.get("owner", "")).strip()
                    or not str(item.get("evidence_ref", "")).strip()
                ):
                    unresolved.append(SECTIONS[key] + "（缺责任人或依据）")
                    continue
                try:
                    datetime.fromisoformat(str(item.get("confirmed_at", "")).replace("Z", "+00:00"))
                except ValueError:
                    unresolved.append(SECTIONS[key] + "（缺确认时间）")
                    continue
                confirmations[key] = {
                    "status": status,
                    "owner": item["owner"],
                    "evidence_ref": item["evidence_ref"],
                    "confirmed_at": item["confirmed_at"],
                }
            if unresolved:
                raise AccountingError("仍有未确认栏目：" + "、".join(unresolved), 422)

            conn.execute(
                "UPDATE fsc_company_profiles SET status='superseded' WHERE firm_id=? AND company_id=? AND status='confirmed'",
                (actor.firm_id, row["company_id"]),
            )
            fingerprint = self._fingerprint(row["industry"], payload, confirmations, row["version"])
            conn.execute(
                "UPDATE fsc_company_profiles SET status='confirmed',confirmations_json=?,fingerprint=?,"
                "confirmed_by=?,confirmed_at=? WHERE id=?",
                (
                    json.dumps(confirmations, ensure_ascii=False, sort_keys=True),
                    fingerprint,
                    actor.user_id,
                    datetime.now().astimezone().isoformat(),
                    profile_id,
                ),
            )
            return self._view(self._row(conn, actor, profile_id))

    def active(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            row = conn.execute(
                "SELECT * FROM fsc_company_profiles WHERE firm_id=? AND company_id=? AND status='confirmed' "
                "ORDER BY version DESC LIMIT 1",
                (actor.firm_id, company_id),
            ).fetchone()
            return self._view(row) if row else None

    def list_profiles(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            rows = conn.execute(
                "SELECT * FROM fsc_company_profiles WHERE firm_id=? AND company_id=? ORDER BY version DESC",
                (actor.firm_id, company_id),
            ).fetchall()
            return {"items": [self._view(row) for row in rows]}

    def matrix(self, actor):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT fc.company_id,c.name AS company_name,"
                "(SELECT p.industry FROM fsc_company_profiles p WHERE p.firm_id=fc.firm_id AND p.company_id=fc.company_id AND p.status='confirmed' "
                " ORDER BY p.version DESC LIMIT 1) AS industry,"
                "(SELECT p.status FROM fsc_company_profiles p WHERE p.firm_id=fc.firm_id AND p.company_id=fc.company_id "
                " ORDER BY p.version DESC LIMIT 1) AS status,"
                "(SELECT p.version FROM fsc_company_profiles p WHERE p.firm_id=fc.firm_id AND p.company_id=fc.company_id "
                " ORDER BY p.version DESC LIMIT 1) AS version,"
                "(SELECT p.confirmations_json FROM fsc_company_profiles p WHERE p.firm_id=fc.firm_id AND p.company_id=fc.company_id AND p.status='confirmed' "
                " ORDER BY p.version DESC LIMIT 1) AS confirmations_json "
                "FROM firm_clients fc JOIN companies c ON c.id=fc.company_id "
                "WHERE fc.firm_id=? AND fc.status='active' ORDER BY c.name",
                (actor.firm_id,),
            ).fetchall()
            items = []
            for row in rows:
                confirmations = (
                    json.loads(row["confirmations_json"]) if row["confirmations_json"] else {}
                )
                items.append(
                    {
                        "company_id": row["company_id"],
                        "company_name": row["company_name"],
                        "industry": row["industry"],
                        "status": row["status"] or "missing",
                        "version": row["version"],
                        "unresolved": [
                            SECTIONS[key] for key in REQUIRED if key not in confirmations
                        ],
                    }
                )
            return {
                "items": items,
                "total": len(items),
                "confirmed": sum(1 for item in items if item["status"] == "confirmed"),
                "unresolved": sum(1 for item in items if item["status"] != "confirmed"),
            }
