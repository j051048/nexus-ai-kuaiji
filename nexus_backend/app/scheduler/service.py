"""Durable scheduler for month-open, accrual and tax reminders.

It only creates reminders or a paused month-open batch. It never posts entries,
closes periods, sends messages or calls a model. A trigger is unique per
plan + scheduled instant so concurrent scanners converge on one row.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError

TZ = ZoneInfo("Asia/Shanghai")
HOLD = "9999-12-31T00:00:00+00:00"
FORBIDDEN = {"sql", "formula", "script", "command", "eval", "exec"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


class PlanWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["monthly_open", "periodic_accrual", "tax_deadline"]
    company_id: str | None = None
    rule: dict
    enabled: bool = True


class SchedulerService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    @staticmethod
    def _normalize(plan: PlanWrite) -> dict:
        raw = plan.rule
        forbidden = sorted(FORBIDDEN.intersection(_walk_keys(raw)))
        if forbidden:
            raise AccountingError("定时规则不能包含可执行字段：" + "、".join(forbidden), 422)
        allowed = {"day", "hour", "minute"}
        if plan.kind == "tax_deadline":
            allowed |= {"deadline_day", "lead_days"}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise AccountingError("定时规则包含未登记字段：" + "、".join(unknown), 422)
        hour, minute = int(raw.get("hour", 8)), int(raw.get("minute", 0))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise AccountingError("定时规则时间不合法", 422)
        if plan.kind == "tax_deadline":
            deadline, lead = int(raw.get("deadline_day", 15)), int(raw.get("lead_days", 3))
            if not (1 <= lead <= 28 and 1 <= deadline <= 28) or deadline - lead < 1:
                raise AccountingError("报税截止日或提前天数不合法", 422)
            return {
                "day": deadline - lead,
                "hour": hour,
                "minute": minute,
                "deadline_day": deadline,
                "lead_days": lead,
            }
        day = int(raw.get("day", 1))
        if not 1 <= day <= 28:
            raise AccountingError("每月触发日须为 1–28", 422)
        return {"day": day, "hour": hour, "minute": minute}

    @staticmethod
    def _next_at(rule: dict, after: datetime) -> str:
        local = after.astimezone(TZ)
        candidate = local.replace(
            day=int(rule["day"]),
            hour=int(rule["hour"]),
            minute=int(rule["minute"]),
            second=0,
            microsecond=0,
        )
        if candidate <= local:
            year = local.year + (1 if local.month == 12 else 0)
            month = 1 if local.month == 12 else local.month + 1
            candidate = candidate.replace(
                year=year,
                month=month,
                day=min(int(rule["day"]), calendar.monthrange(year, month)[1]),
            )
        return candidate.astimezone(timezone.utc).isoformat()

    def _company_ok(self, conn, firm_id: str, company_id: str | None) -> bool:
        if not company_id:
            return True
        return (
            conn.execute(
                "SELECT 1 FROM firm_clients WHERE firm_id=? AND company_id=?", (firm_id, company_id)
            ).fetchone()
            is not None
        )

    def _audit(self, conn, *, firm_id, company_id, user_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,company_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                firm_id,
                None,
                company_id,
                user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )

    @staticmethod
    def _rule(value):
        return json.loads(value) if isinstance(value, str) else value

    @classmethod
    def _plan_view(cls, row) -> dict:
        return {
            "id": row["id"],
            "firm_id": row["firm_id"],
            "company_id": row["company_id"],
            "kind": row["kind"],
            "rule": cls._rule(row["rule_json"]),
            "next_run_at": row["next_run_at"],
            "enabled": bool(row["enabled"]),
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_plan(self, actor, request: PlanWrite) -> dict:
        rule = self._normalize(request)
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            if request.company_id and not self._company_ok(conn, actor.firm_id, request.company_id):
                raise AccountingError("企业不属于当前事务所", 404)
            plan_id, stamp = uuid4().hex, _now()
            conn.execute(
                "INSERT INTO fsc_schedule_plans(id,firm_id,company_id,kind,rule_json,next_run_at,enabled,version,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?,?)",
                (
                    plan_id,
                    actor.firm_id,
                    request.company_id,
                    request.kind,
                    json.dumps(rule, ensure_ascii=False),
                    self._next_at(rule, datetime.now(timezone.utc)),
                    bool(request.enabled),
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            self._audit(
                conn,
                firm_id=actor.firm_id,
                company_id=request.company_id,
                user_id=actor.user_id,
                action="scheduler.plan.create",
                object_id=plan_id,
                details={"kind": request.kind, "rule": rule, "enabled": request.enabled},
            )
            row = conn.execute("SELECT * FROM fsc_schedule_plans WHERE id=?", (plan_id,)).fetchone()
        return self._plan_view(row)

    def set_enabled(self, actor, plan_id: str, enabled: bool) -> dict:
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM fsc_schedule_plans WHERE id=? AND firm_id=?",
                (plan_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("定时计划不存在", 404)
            conn.execute(
                "UPDATE fsc_schedule_plans SET enabled=?,version=version+1,updated_at=? WHERE id=? AND firm_id=?",
                (bool(enabled), _now(), plan_id, actor.firm_id),
            )
            self._audit(
                conn,
                firm_id=actor.firm_id,
                company_id=row["company_id"],
                user_id=actor.user_id,
                action="scheduler.plan.disable" if not enabled else "scheduler.plan.enable",
                object_id=plan_id,
                details={"version": row["version"] + 1},
            )
            row = conn.execute("SELECT * FROM fsc_schedule_plans WHERE id=?", (plan_id,)).fetchone()
        return self._plan_view(row)

    def list_plans(self, actor) -> list[dict]:
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT * FROM fsc_schedule_plans WHERE firm_id=? ORDER BY created_at DESC,id",
                (actor.firm_id,),
            ).fetchall()
        return [self._plan_view(row) for row in rows]

    def tick(self, now: datetime | None = None) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self.db.transaction(actor=None, system=True) as conn:
            plans = conn.execute(
                "SELECT id FROM fsc_schedule_plans WHERE enabled=TRUE AND next_run_at<=? ORDER BY next_run_at,id",
                (now.isoformat(),),
            ).fetchall()
        created = skipped = 0
        for candidate in plans:
            result = self._tick_plan(candidate["id"], now)
            created += result == "created"
            skipped += result == "skipped"
        return {"triggers": created, "skipped": skipped, "at": now.isoformat()}

    def _tick_plan(self, plan_id: str, now: datetime) -> str:
        with self.db.transaction(write=True, actor=None, system=True) as conn:
            if self.db.is_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?))", (f"fsc-schedule:{plan_id}",)
                )
            plan = conn.execute(
                "SELECT * FROM fsc_schedule_plans WHERE id=?", (plan_id,)
            ).fetchone()
            if not plan or not plan["enabled"] or plan["next_run_at"] > now.isoformat():
                return "skipped"
            if not self._company_ok(conn, plan["firm_id"], plan["company_id"]):
                conn.execute(
                    "UPDATE fsc_schedule_plans SET enabled=FALSE,version=version+1,updated_at=? WHERE id=?",
                    (_now(), plan_id),
                )
                return "skipped"
            scheduled_for = self._latest_due(
                self._rule(plan["rule_json"]), plan["next_run_at"], now
            )
            if conn.execute(
                "SELECT 1 FROM fsc_schedule_triggers WHERE dedupe_key=?",
                (f"{plan_id}:{scheduled_for}",),
            ).fetchone():
                return "skipped"
            trigger_id, reminder_id, stamp = uuid4().hex, uuid4().hex, _now()
            local = datetime.fromisoformat(scheduled_for).astimezone(TZ)
            title, detail = self._copy(plan, local)
            conn.execute(
                "INSERT INTO fsc_schedule_triggers(id,plan_id,firm_id,company_id,kind,scheduled_for,dedupe_key,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,'pending',?)",
                (
                    trigger_id,
                    plan_id,
                    plan["firm_id"],
                    plan["company_id"],
                    plan["kind"],
                    scheduled_for,
                    f"{plan_id}:{scheduled_for}",
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO fsc_schedule_reminders(id,trigger_id,firm_id,company_id,kind,title,detail,status,due_at,created_at) "
                "VALUES(?,?,?,?,?,?,?,'open',?,?)",
                (
                    reminder_id,
                    trigger_id,
                    plan["firm_id"],
                    plan["company_id"],
                    plan["kind"],
                    title,
                    detail,
                    local.date().isoformat(),
                    stamp,
                ),
            )
            if plan["kind"] == "monthly_open" and plan["company_id"]:
                batch_id = self._create_paused_batch(conn, plan, trigger_id, local, stamp)
                conn.execute(
                    "UPDATE fsc_schedule_triggers SET batch_id=? WHERE id=?", (batch_id, trigger_id)
                )
            conn.execute(
                "UPDATE fsc_schedule_plans SET next_run_at=?,updated_at=? WHERE id=?",
                (self._next_at(self._rule(plan["rule_json"]), now), stamp, plan_id),
            )
        return "created"

    def _latest_due(self, rule: dict, next_run_at: str, now: datetime) -> str:
        due = datetime.fromisoformat(next_run_at)
        for _ in range(400):
            following = datetime.fromisoformat(self._next_at(rule, due))
            if following > now:
                break
            due = following
        return due.isoformat()

    def _copy(self, plan, local: datetime):
        if plan["kind"] == "monthly_open":
            return "月初开账待确认", f"{local:%Y-%m} 月初开账已准备，确认后才会进入批量队列。"
        if plan["kind"] == "periodic_accrual":
            return (
                "周期计提待办理",
                f"{local:%Y-%m} 周期计提/折旧到点提醒；请前往既有 periodic 入口人工生成。",
            )
        rule = self._rule(plan["rule_json"])
        return (
            "报税期限提醒",
            f"本月申报截止日 {rule['deadline_day']} 日，提前 {rule['lead_days']} 天提醒；请人工办理。",
        )

    def _create_paused_batch(self, conn, plan, trigger_id, local, stamp):
        batch_id = uuid4().hex
        company = conn.execute(
            "SELECT name FROM companies WHERE id=?", (plan["company_id"],)
        ).fetchone()
        period = local.strftime("%Y-%m")
        request = {
            "period": period,
            "scope": "companies",
            "companies": [plan["company_id"]],
            "reason": f"定时开账 {period}",
        }
        conn.execute(
            "INSERT INTO batch_operations(id,firm_id,kind,status,idempotency_key,request_json,total,succeeded,skipped,failed,"
            "created_by,created_at,updated_at,started_at,finished_at) VALUES(?,?,'monthly_open','queued',?,?,1,0,0,0,?,?,?,NULL,NULL)",
            (
                batch_id,
                plan["firm_id"],
                f"scheduler:{trigger_id}",
                json.dumps(request, ensure_ascii=False),
                plan["created_by"],
                stamp,
                stamp,
            ),
        )
        conn.execute(
            "INSERT INTO batch_operation_items(id,batch_id,firm_id,company_id,company_name,sequence_no,status,attempt,"
            "max_attempts,available_at,result_json,error_code,error_detail,created_at,updated_at) "
            "VALUES(?,?,?,?,?,1,'queued',0,3,?,'{}','','',?,?)",
            (
                uuid4().hex,
                batch_id,
                plan["firm_id"],
                plan["company_id"],
                company["name"] if company else "",
                HOLD,
                stamp,
                stamp,
            ),
        )
        return batch_id

    def triggers(self, actor, plan_id: str) -> list[dict]:
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            if not conn.execute(
                "SELECT 1 FROM fsc_schedule_plans WHERE id=? AND firm_id=?",
                (plan_id, actor.firm_id),
            ).fetchone():
                raise AccountingError("定时计划不存在", 404)
            rows = conn.execute(
                "SELECT t.*,r.kind AS reminder_kind,r.title,r.detail,r.status AS reminder_status,r.due_at FROM fsc_schedule_triggers t "
                "JOIN fsc_schedule_reminders r ON r.trigger_id=t.id WHERE t.plan_id=? ORDER BY t.scheduled_for DESC,t.id",
                (plan_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "plan_id": row["plan_id"],
                "kind": row["kind"],
                "scheduled_for": row["scheduled_for"],
                "status": row["status"],
                "batch_id": row["batch_id"],
                "reminder": {
                    "kind": row["reminder_kind"],
                    "title": row["title"],
                    "detail": row["detail"],
                    "status": row["reminder_status"],
                    "due_at": row["due_at"],
                },
            }
            for row in rows
        ]

    def confirm(self, actor, trigger_id: str) -> dict:
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM fsc_schedule_triggers WHERE id=? AND firm_id=?",
                (trigger_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("定时触发不存在", 404)
            if row["status"] != "pending":
                raise AccountingError("该触发已处理", 409)
            stamp = _now()
            if row["batch_id"]:
                conn.execute(
                    "UPDATE batch_operation_items SET available_at=?,updated_at=? WHERE batch_id=?",
                    (stamp, stamp, row["batch_id"]),
                )
                conn.execute(
                    "UPDATE batch_operations SET updated_at=? WHERE id=?", (stamp, row["batch_id"])
                )
            conn.execute(
                "UPDATE fsc_schedule_triggers SET status='confirmed',confirmed_at=? WHERE id=?",
                (stamp, trigger_id),
            )
            conn.execute(
                "UPDATE fsc_schedule_reminders SET status='confirmed',confirmed_at=? WHERE trigger_id=?",
                (stamp, trigger_id),
            )
            self._audit(
                conn,
                firm_id=actor.firm_id,
                company_id=row["company_id"],
                user_id=actor.user_id,
                action="scheduler.trigger.confirm",
                object_id=trigger_id,
                details={"batch_id": row["batch_id"]},
            )
        return {"id": trigger_id, "status": "confirmed", "batch_id": row["batch_id"]}
