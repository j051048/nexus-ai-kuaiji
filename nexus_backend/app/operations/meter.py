"""Budget reservations cover uncertain requests until an authorized reconciliation."""
from datetime import datetime, timezone, timedelta
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..business.service import encode
from .pricing import Pricing, MAX_TOKENS

BILLING_TIMEZONE = timezone(timedelta(hours=8))


class BudgetError(AccountingError):
    def __init__(self, code):
        messages = {"pricing_missing": "该企业启用了费用上限，但模型单价尚未配置，请联系管理员",
                    "quota_exhausted": "该企业本月模型额度不足，请联系管理员调整或核定待确认费用",
                    "quota_overrun": "该企业出现超预留费用，请管理员核定后再发起模型请求",
                    "policy_invalid": "企业费用配置不完整，请联系管理员"}
        self.code = code
        super().__init__(messages.get(code, "模型费用预留未通过，请核对当前权限与配置"), 429 if code == "quota_exhausted" else 409)


class MeterService:
    def __init__(self, db, pricing=None, clock=None):
        self.db = db
        self.pricing = pricing or Pricing.from_env()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def timestamp(self, conn=None):
        if self.db.is_postgres and conn is not None:
            return conn.execute("SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')").fetchone()[0]
        return self.clock().astimezone(timezone.utc).isoformat()

    def period(self, conn=None):
        if self.db.is_postgres and conn is not None:
            return conn.execute("SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM')").fetchone()[0]
        return self.clock().astimezone(BILLING_TIMEZONE).strftime("%Y-%m")

    def context(self, actor, run):
        return MeterContext(self, actor, dict(run), uuid4().hex)

    @staticmethod
    def exposure(rows):
        return sum(0 if row["state"] == "released" else row["confirmed_micro"] if row["confirmed_micro"] is not None else row["estimated_micro"] if row["estimated_micro"] is not None else row["reserved_micro"] for row in rows)

    def reserve(self, conn, actor, run, token):
        pricing = self.pricing.for_kind(run["kind"]).snapshot(run["provider"], run["model"])
        if self.db.is_postgres:
            function = "reserve_assistant_model_cost" if run["kind"] == "chat" else "reserve_intake_model_cost" if run.get("intake_id") else "reserve_model_cost"
            state = conn.execute(f"SELECT accounting.{function}(?,?,?,?,?,?)", (run["id"], token, run["provider"], run["model"], encode(pricing), 0)).fetchone()[0]
            if state not in {"reserved", "sent", "estimated", "uncertain", "released", "reconciled"}:
                raise BudgetError(state)
            return
        if run["created_by"] != actor.user_id or run["firm_id"] != actor.firm_id:
            raise BudgetError("authorization_changed")
        previous = conn.execute("SELECT charge_token FROM model_costs WHERE id=?", (run["id"],)).fetchone()
        if previous:
            if previous["charge_token"] != token:
                raise BudgetError("token_mismatch")
            return
        policy = conn.execute("SELECT * FROM model_company_policies WHERE firm_id=? AND company_id=?", (actor.firm_id, run["company_id"])).fetchone()
        enabled = bool(policy and policy["quota_enabled"])
        reserve = policy[{"ocr": "ocr_reserve_micro", "proposal": "proposal_reserve_micro", "chat": "chat_reserve_micro"}[run["kind"]]] if enabled else 0
        period = self.period(conn)
        if enabled:
            if not pricing["configured"]:
                raise BudgetError("pricing_missing")
            if policy["monthly_limit_micro"] <= 0 or reserve <= 0:
                raise BudgetError("policy_invalid")
            if conn.execute("SELECT 1 FROM model_costs WHERE firm_id=? AND company_id=? AND overrun=1", (actor.firm_id, run["company_id"])).fetchone():
                raise BudgetError("quota_overrun")
            rows = conn.execute("SELECT * FROM model_costs WHERE firm_id=? AND company_id=? AND period=?", (actor.firm_id, run["company_id"], period)).fetchall()
            carried = conn.execute("SELECT COALESCE(SUM(reserved_micro),0) FROM model_costs WHERE firm_id=? AND company_id=? AND period<? AND state IN ('reserved','sent','uncertain')", (actor.firm_id, run["company_id"], period)).fetchone()[0]
            if self.exposure(rows) + carried + reserve > policy["monthly_limit_micro"]:
                raise BudgetError("quota_exhausted")
        stamp = self.timestamp(conn)
        intake = bool(run.get("intake_id"))
        chat = run["kind"] == "chat"
        conn.execute("INSERT INTO model_costs(id,run_id,intake_run_id,chat_turn_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,state,version,created_at,overrun,manual_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'reserved',1,?,0,'')", (run["id"], None if intake or chat else run["id"], run["id"] if intake else None, run["id"] if chat else None, actor.firm_id, run["company_id"], run.get("event_id"), run["kind"], actor.user_id, period, run["provider"], run["model"], token, encode(pricing), int(enabled), reserve, stamp))

    def mark_sent(self, conn, actor, run, token):
        if self.db.is_postgres:
            valid = conn.execute("SELECT accounting.mark_model_cost_sent(?,?)", (run["id"], token)).fetchone()[0]
        else:
            valid = conn.execute("UPDATE model_costs SET state='sent',sent_at=?,version=version+1 WHERE id=? AND charge_token=? AND created_by=? AND firm_id=? AND state='reserved'", (self.timestamp(conn), run["id"], token, actor.user_id, actor.firm_id)).rowcount == 1
        if not valid:
            raise BudgetError("authorization_changed")

    def settle(self, actor, run, token, usage=None, safe_rejection=False):
        values = (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
        if any(type(value) is not int or not 0 <= value <= MAX_TOKENS for value in values):
            values = (None, None)
        with self.db.transaction(write=True, actor=actor) as conn:
            if self.db.is_postgres:
                return conn.execute("SELECT accounting.settle_model_cost(?,?,?,?,?)", (run["id"], token, *values, safe_rejection)).fetchone()[0]
            row = conn.execute("SELECT * FROM model_costs WHERE id=? AND charge_token=? AND created_by=? AND firm_id=?", (run["id"], token, actor.user_id, actor.firm_id)).fetchone()
            if not row or row["state"] not in {"reserved", "sent", "uncertain"}:
                return False
            pricing = json.loads(row["pricing_json"])
            state, estimate = "uncertain", None
            if safe_rejection:
                state, estimate = "released", 0
            elif all(value is not None for value in values) and pricing["configured"]:
                estimate = (values[0] * pricing["input_per_million_micro"] + values[1] * pricing["output_per_million_micro"] + 999_999) // 1_000_000
                state = "estimated"
            overrun = bool(row["quota_enabled"] and estimate is not None and estimate > row["reserved_micro"])
            conn.execute("UPDATE model_costs SET state=?,estimated_micro=?,input_tokens=?,output_tokens=?,overrun=?,version=version+1,finished_at=? WHERE id=? AND charge_token=?", (state, estimate, *values, int(overrun), self.timestamp(conn), run["id"], token))
            return True


class MeterContext:
    def __init__(self, meter, actor, run, token):
        self.meter, self.actor, self.run, self.token = meter, actor, run, token
        self.reserved = False
        self.sent = False

    def before_send(self, conn):
        self.meter.reserve(conn, self.actor, self.run, self.token)
        self.reserved = True
        self.meter.mark_sent(conn, self.actor, self.run, self.token)
        self.sent = True

    def finish(self, result=None, error=None, attempted=False):
        if not self.reserved:
            return
        usage = result.usage if result else getattr(error, "usage", None)
        local_rejections = {"busy", "unavailable", "document_invalid", "document_too_large", "context_invalid", "context_too_large"}
        safe = not attempted or error is not None and (error.code in local_rejections or error.retryable)
        # Cost facts may be recorded after accounting permission is revoked.
        # Only this request's opaque token can settle; no original data is read.
        try:
            self.meter.settle(self.actor, self.run, self.token, usage, safe_rejection=bool(safe))
        except Exception:
            # A sent reservation remains held and is surfaced by monitoring;
            # never release it because the DB acknowledgement is uncertain.
            pass
