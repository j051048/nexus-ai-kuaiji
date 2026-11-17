"""FSC05: versioned evidence policy evaluated by the authoritative service.

Policy data says which evidence is required for a business action; it never
contains executable rules. Evidence facts are supplied by callers, and the
service returns blockers rather than letting a page turn an attachment into a
trusted approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError

ACTIONS = {"receive", "draft", "accrual", "revenue", "payment", "review", "month_close", "export"}
STATUSES = {"missing", "pending", "verified", "expired", "conflict"}
FORBIDDEN = {"sql", "formula", "script", "command", "eval", "exec"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json(value: Any):
    return value if isinstance(value, (list, dict)) else json.loads(value)


def _forbidden(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in FORBIDDEN:
                raise AccountingError(f"证据政策不允许可执行配置：{path}{key}", 422)
            _forbidden(item, f"{path}{key}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _forbidden(item, f"{path}[{index}].")


def _clock(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class Requirement(BaseModel):
    action: str
    business_type: str = "*"
    evidence_kind: str
    required: bool = True
    max_age_days: int | None = None
    min_amount_cents: int = 0


class ExceptionGrant(BaseModel):
    action: str
    business_type: str = "*"
    evidence_kind: str
    reason: str
    approved_by: str
    expires_on: str | None = None
    max_amount_cents: int = 0


class EvidencePolicyWrite(BaseModel):
    industry_code: str
    requirements: list[Requirement] = Field(default_factory=list)
    exceptions: list[ExceptionGrant] = Field(default_factory=list)


class PolicyConfirm(BaseModel):
    expected_version: int


class EvidenceFact(BaseModel):
    evidence_kind: str
    status: Literal["missing", "pending", "verified", "expired", "conflict"] = "pending"
    reference: str = ""
    captured_at: datetime | str | None = None
    verified_by: str = ""
    source_system: str = ""


class EvidenceEvaluate(BaseModel):
    action: str
    business_type: str
    amount_cents: int = 0
    evidence: list[EvidenceFact] = Field(default_factory=list)


class EvidenceRequest(EvidenceEvaluate):
    due_on: str | None = None


class FscEvidenceService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    def create_draft(self, actor, company_id: str, request: EvidencePolicyWrite):
        if not request.industry_code.strip():
            raise AccountingError("证据政策缺少行业", 422)
        if not request.requirements:
            raise AccountingError("证据政策至少需要一条证据要求", 422)
        for req in request.requirements:
            if req.action not in ACTIONS:
                raise AccountingError(f"未知办理动作：{req.action}", 422)
            if not req.evidence_kind.strip() or not req.business_type.strip():
                raise AccountingError("证据要求缺少类型或证据种类", 422)
            if req.max_age_days is not None and req.max_age_days < 1:
                raise AccountingError("证据有效期必须为正天数", 422)
            if req.min_amount_cents < 0:
                raise AccountingError("证据金额门槛不能为负", 422)
        for grant in request.exceptions:
            if grant.action not in ACTIONS or not grant.evidence_kind.strip():
                raise AccountingError("合法例外缺少动作或证据种类", 422)
            if len(grant.reason.strip()) < 5 or not grant.approved_by.strip():
                raise AccountingError("合法例外必须记录依据和独立审批人", 422)
            if grant.max_amount_cents < 0:
                raise AccountingError("例外金额上限不能为负", 422)
        _forbidden(request.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            last = conn.execute(
                "SELECT MAX(version) AS version FROM fsc_evidence_policies WHERE firm_id=? AND company_id=? AND industry_code=?",
                (actor.firm_id, company_id, request.industry_code),
            ).fetchone()
            version = int(last["version"] or 0) + 1
            conn.execute(
                "UPDATE fsc_evidence_policies SET status='superseded',superseded_at=? "
                "WHERE firm_id=? AND company_id=? AND industry_code=? AND status='draft'",
                (_now(), actor.firm_id, company_id, request.industry_code),
            )
            policy_id = str(uuid4())
            body = request.model_dump()
            fingerprint = _digest(body)
            conn.execute(
                "INSERT INTO fsc_evidence_policies("
                "id,firm_id,company_id,industry_code,version,status,requirements_json,exceptions_json,"
                "fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    policy_id,
                    actor.firm_id,
                    company_id,
                    request.industry_code,
                    version,
                    "draft",
                    json.dumps(body["requirements"], ensure_ascii=False),
                    json.dumps(body["exceptions"], ensure_ascii=False),
                    fingerprint,
                    actor.user_id,
                    _now(),
                ),
            )
            self._event(conn, policy_id, "drafted", actor, {"version": version})
            return self._view(self._row(conn, actor, policy_id))

    def confirm(self, actor, policy_id: str, expected_version: int):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, policy_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, policy_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if row["status"] != "draft":
                raise AccountingError("只有草稿证据政策可以确认", 409)
            if int(row["version"]) != int(expected_version):
                raise AccountingError("证据政策版本已变化，请刷新后重新核对", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("证据政策必须由另一名管理员独立确认", 409)
            conn.execute(
                "UPDATE fsc_evidence_policies SET status='confirmed',confirmed_by=?,confirmed_at=? WHERE id=?",
                (actor.user_id, _now(), policy_id),
            )
            self._event(conn, policy_id, "confirmed", actor)
            return self._view(self._row(conn, actor, policy_id))

    def activate(self, actor, policy_id: str, expected_version: int):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, policy_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, policy_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if row["status"] != "confirmed":
                raise AccountingError("只有已确认证据政策可以生效", 409)
            if int(row["version"]) != int(expected_version):
                raise AccountingError("证据政策版本已变化，请刷新后重新核对", 409)
            profile = conn.execute(
                "SELECT id FROM fsc_company_profiles WHERE company_id=? AND firm_id=? AND status='confirmed' AND industry=?",
                (row["company_id"], actor.firm_id, row["industry_code"]),
            ).fetchone()
            if not profile:
                raise AccountingError("企业行业档案尚未确认，不能启用到生效", 409)
            if self.db.is_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?)::bigint)",
                    (f"fsc-evidence:{row['company_id']}:{row['industry_code']}",),
                )
            previous = conn.execute(
                "SELECT id FROM fsc_evidence_policies "
                "WHERE firm_id=? AND company_id=? AND industry_code=? AND status='active'",
                (actor.firm_id, row["company_id"], row["industry_code"]),
            ).fetchall()
            for old in previous:
                conn.execute(
                    "UPDATE fsc_evidence_policies SET status='superseded',superseded_at=? WHERE id=?",
                    (_now(), old["id"]),
                )
                self._event(conn, old["id"], "superseded", actor, {"by": policy_id})
            conn.execute(
                "UPDATE fsc_evidence_policies SET status='active',activated_by=?,activated_at=? WHERE id=?",
                (actor.user_id, _now(), policy_id),
            )
            self._event(conn, policy_id, "activated", actor)
            return self._view(self._row(conn, actor, policy_id))

    def deactivate(self, actor, policy_id: str):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, policy_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, policy_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if row["status"] != "active":
                raise AccountingError("只有生效中的证据政策可以停用", 409)
            conn.execute(
                "UPDATE fsc_evidence_policies SET status='inactive' WHERE id=?", (policy_id,)
            )
            self._event(conn, policy_id, "deactivated", actor)
            return self._view(self._row(conn, actor, policy_id))

    def list(self, actor, company_id: str):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            self.access.company(conn, actor, company_id)
            rows = conn.execute(
                "SELECT * FROM fsc_evidence_policies WHERE company_id=? AND firm_id=? "
                "ORDER BY industry_code,version DESC",
                (company_id, actor.firm_id),
            ).fetchall()
            return {"items": [self._view(row) for row in rows]}

    def evaluate(self, actor, book_id: str, request: EvidenceEvaluate):
        if request.action not in ACTIONS:
            raise AccountingError(f"未知办理动作：{request.action}", 422)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            row = conn.execute(
                "SELECT * FROM fsc_evidence_policies WHERE company_id=? AND firm_id=? AND status='active' "
                "ORDER BY version DESC LIMIT 1",
                (book["company_id"], actor.firm_id),
            ).fetchone()
            if not row:
                return {
                    "allowed": False,
                    "policy_id": None,
                    "policy_version": None,
                    "blockers": [
                        {
                            "code": "policy_missing",
                            "evidence_kind": "",
                            "message": "企业尚未核定生效的证据政策",
                        }
                    ],
                }
            requirements = _json(row["requirements_json"])
            grants = _json(row["exceptions_json"])
            evidence = {fact.evidence_kind: fact for fact in request.evidence}
            blockers: list[dict[str, Any]] = []
            applied: list[dict[str, Any]] = []
            now = datetime.now(timezone.utc)
            for req in requirements:
                if not req.get("required", True):
                    continue
                if req.get("action") != request.action:
                    continue
                if req.get("business_type") not in ("*", request.business_type):
                    continue
                if int(request.amount_cents) < int(req.get("min_amount_cents", 0)):
                    continue
                kind = req["evidence_kind"]
                fact = evidence.get(kind)
                code = ""
                if fact is None or fact.status == "missing":
                    code = "missing"
                elif fact.status != "verified":
                    code = fact.status
                elif req.get("max_age_days") and fact.captured_at:
                    captured = _clock(fact.captured_at)
                    if captured and (now - captured).days > int(req["max_age_days"]):
                        code = "expired"
                if not code:
                    continue
                grant = next(
                    (
                        item
                        for item in grants
                        if item.get("action") == request.action
                        and item.get("business_type") in ("*", request.business_type)
                        and item.get("evidence_kind") == kind
                        and item.get("approved_by")
                        and item.get("reason")
                        and (
                            not item.get("expires_on")
                            or str(item["expires_on"]) >= now.date().isoformat()
                        )
                        and (
                            int(item.get("max_amount_cents", 0)) == 0
                            or int(request.amount_cents) <= int(item["max_amount_cents"])
                        )
                    ),
                    None,
                )
                if grant:
                    applied.append(
                        {
                            "evidence_kind": kind,
                            "reason": grant["reason"],
                            "approved_by": grant["approved_by"],
                        }
                    )
                    continue
                blockers.append(
                    {
                        "code": code,
                        "evidence_kind": kind,
                        "message": {
                            "missing": "缺少必需证据",
                            "pending": "证据尚未核验",
                            "expired": "证据已过期",
                            "conflict": "证据存在冲突",
                        }.get(code, "证据不可用"),
                    }
                )
            return {
                "allowed": not blockers,
                "policy_id": row["id"],
                "policy_version": int(row["version"]),
                "policy_fingerprint": row["fingerprint"],
                "blockers": blockers,
                "exceptions_applied": applied,
            }

    def request_missing(self, actor, book_id: str, request: EvidenceRequest):
        """Create ordinary monthly material requests for policy blockers.

        The evidence policy remains the authority. This only opens the existing
        material-request loop; it never marks evidence verified or grants book
        access to the assignee.
        """
        if request.action not in ACTIONS:
            raise AccountingError(f"未知办理动作：{request.action}", 422)
        evaluation = self.evaluate(actor, book_id, request)
        if evaluation.get("allowed"):
            raise AccountingError("当前证据已满足政策，无需补件", 409)
        policy_id = evaluation.get("policy_id")
        if not policy_id:
            raise AccountingError("请先核定生效的证据政策", 409)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, "edit")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
            self.access.require_manager(conn, actor)
            self.access.company(conn, actor, book["company_id"], manage=True)
            policy = conn.execute(
                "SELECT * FROM fsc_evidence_policies WHERE id=? AND firm_id=? AND status='active'",
                (policy_id, actor.firm_id),
            ).fetchone()
            if not policy:
                raise AccountingError("证据政策已变化，请重新评估", 409)
            plan = conn.execute(
                "SELECT contact_user_id FROM service_plan_versions WHERE book_id=? "
                "ORDER BY version DESC LIMIT 1",
                (book_id,),
            ).fetchone()
            assignee = plan["contact_user_id"] if plan else None
            if not assignee:
                raise AccountingError("请先在本月服务单设置资料联系人，再生成补件任务", 409)
            ledger = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=? LIMIT 1", (book_id,)
            ).fetchone()
            if not ledger:
                raise AccountingError("账套缺少长期账本，无法生成补件任务", 409)
            due_on = request.due_on or f"{book['period']}-28"
            if due_on != f"{book['period']}-28":
                raise AccountingError("补件期限必须按本月服务单截止日（每月 28 日）", 422)
            created: list[dict[str, Any]] = []
            reused: list[dict[str, Any]] = []
            seen: set[str] = set()
            for blocker in evaluation.get("blockers", []):
                kind = (blocker.get("evidence_kind") or "").strip()
                if not kind or kind in seen:
                    continue
                seen.add(kind)
                existing = conn.execute(
                    "SELECT r.* FROM fsc_evidence_request_links l "
                    "JOIN material_requests r ON r.id=l.request_id "
                    "WHERE l.book_id=? AND l.policy_id=? AND l.action=? AND l.business_type=? AND l.evidence_kind=?",
                    (book_id, policy_id, request.action, request.business_type, kind),
                ).fetchone()
                if existing:
                    reused.append(self._request_view(existing))
                    continue
                title = f"补交证据：{kind}"[:120]
                description = (
                    f"{request.action}/{request.business_type}：{blocker.get('message', '证据不可用')}"
                    f"（{kind}，政策 v{int(policy['version'])}）"
                )[:1000]
                item_key = (
                    "fsc-"
                    + _digest(
                        {
                            "policy": policy_id,
                            "action": request.action,
                            "business_type": request.business_type,
                            "kind": kind,
                        }
                    )[:20]
                )
                template_name = f"FSC 证据补件 {kind}"[:120]
                template = conn.execute(
                    "SELECT id FROM material_templates WHERE book_id=? AND name=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (book_id, template_name),
                ).fetchone()
                if template:
                    template_id = template["id"]
                else:
                    template_id = uuid4().hex
                    item = {
                        "key": item_key,
                        "title": title,
                        "description": description,
                        "required": True,
                        "due_month_offset": 0,
                        "due_day": 28,
                        "assignee_user_id": assignee,
                    }
                    conn.execute(
                        "INSERT INTO material_templates(id,book_id,ledger_id,firm_id,company_id,version,"
                        "name,items_json,reason,created_by,created_at) VALUES(?,?,?,?,?,1,?,?,?,?,?)",
                        (
                            template_id,
                            book_id,
                            ledger["ledger_id"],
                            actor.firm_id,
                            book["company_id"],
                            template_name,
                            json.dumps([item], ensure_ascii=False),
                            "证据政策缺件自动生成补件任务",
                            actor.user_id,
                            _now(),
                        ),
                    )
                request_id = uuid4().hex
                conn.execute(
                    "INSERT INTO material_requests(id,book_id,firm_id,company_id,period,template_id,item_key,"
                    "title,description,required,due_on,assignee_user_id,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                    (
                        request_id,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        book["period"],
                        template_id,
                        item_key,
                        title,
                        description,
                        due_on,
                        assignee,
                        actor.user_id,
                        _now(),
                    ),
                )
                conn.execute(
                    "INSERT INTO fsc_evidence_request_links(request_id,firm_id,company_id,book_id,policy_id,"
                    "policy_version,action,business_type,evidence_kind,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        actor.firm_id,
                        book["company_id"],
                        book_id,
                        policy_id,
                        int(policy["version"]),
                        request.action,
                        request.business_type,
                        kind,
                        actor.user_id,
                        _now(),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM material_requests WHERE id=?", (request_id,)
                ).fetchone()
                created.append(self._request_view(row))
            return {
                "policy_version": int(policy["version"]),
                "policy_fingerprint": policy["fingerprint"],
                "created": len(created),
                "reused": len(reused),
                "requests": created + reused,
            }

    @staticmethod
    def _request_view(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "due_on": row["due_on"],
            "assignee_user_id": row["assignee_user_id"],
            "period": row["period"],
            "book_id": row["book_id"],
        }

    def active_matrix(self, actor):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT p.*,c.name AS company_name FROM fsc_evidence_policies p "
                "JOIN companies c ON c.id=p.company_id "
                "WHERE p.firm_id=? AND p.status='active' ORDER BY c.name,p.industry_code",
                (actor.firm_id,),
            ).fetchall()
            return {
                "items": [{**self._view(row), "company_name": row["company_name"]} for row in rows]
            }

    def _row(self, conn, actor, policy_id: str):
        row = conn.execute(
            "SELECT * FROM fsc_evidence_policies WHERE id=? AND firm_id=?",
            (policy_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("证据政策不存在", 404)
        return row

    def _event(self, conn, policy_id: str, event: str, actor, detail: dict | None = None):
        conn.execute(
            "INSERT INTO fsc_evidence_policy_events(id,policy_id,event,actor_id,at,detail_json) "
            "VALUES(?,?,?,?,?,?)",
            (
                str(uuid4()),
                policy_id,
                event,
                actor.user_id,
                _now(),
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )

    @staticmethod
    def _view(row):
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "industry_code": row["industry_code"],
            "version": int(row["version"]),
            "status": row["status"],
            "requirements": _json(row["requirements_json"]),
            "exceptions": _json(row["exceptions_json"]),
            "fingerprint": row["fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"],
            "activated_at": row["activated_at"],
        }
