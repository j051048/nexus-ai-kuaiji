"""Company operating summaries never open private employee evidence scopes."""

from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from ..accounting.access import AccessControl, FIRM_MANAGERS
from ..accounting.domain import AccountingError
from ..accounting.models import cents_to_money
from ..accounting.service import fingerprint
from ..business.service import BusinessService, encode
from ..intelligence.provider import effective_settings
from .pricing import decimal_micro, micro_decimal

OPS_ROLES = {"owner", "admin", "finance"}
HELD_STATES = {"reserved", "sent", "uncertain"}
QUEUE_STATES = ("queued", "running", "retry_wait", "succeeded", "failed", "interrupted", "stale", "cancelled")
DEFAULT_POLICY = {
    "quota_enabled": 0, "monthly_limit_micro": 0, "ocr_reserve_micro": 0, "proposal_reserve_micro": 0, "chat_reserve_micro": 0,
    "failure_threshold": 3, "failure_window_minutes": 60, "backlog_threshold": 10,
    "backlog_age_minutes": 60, "queue_wait_minutes": 10, "worker_stale_seconds": 90, "version": 0,
}


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _date(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _sum_present(rows, field):
    values = [row[field] for row in rows if row[field] is not None]
    return sum(values) if values else None


def record_exception_snapshot(db, conn, actor, event, active_findings, context_fp):
    """Persist counts already computed in an authorized evidence transaction."""
    enabled = conn.execute("SELECT enabled FROM ai_company_settings WHERE firm_id=? AND company_id=?",
                           (event["firm_id"], event["company_id"])).fetchone()
    active = conn.execute("SELECT attachment_id FROM ocr_jobs WHERE event_id=? AND status IN ('queued','running','retry_wait')", (event["id"],)).fetchall()
    pending = {row["attachment_id"] for row in active}
    findings = []
    if enabled and enabled["enabled"]:
        for item in active_findings:
            payload = item.get("payload") or {}
            if item.get("resolved") or payload.get("non_overridable"):
                continue
            if (item["code"].startswith("ocr_missing_") or item["code"] == "ocr_running") and payload.get("attachment_id") in pending:
                continue
            findings.append(item)
    blocking = sum(item["severity"] == "blocking" for item in findings)
    warning = sum(item["severity"] == "warning" for item in findings)
    if db.is_postgres:
        return conn.execute("SELECT accounting.record_ops_exception_snapshot(?,?,?,?,?)",
                            (event["id"], blocking, warning, None, context_fp)).fetchone()[0]
    stamp = datetime.now(timezone.utc).isoformat()
    prior = conn.execute("SELECT * FROM ops_exception_snapshots WHERE event_id=?", (event["id"],)).fetchone()
    if prior and prior["blocking_count"] == blocking and prior["warning_count"] == warning and prior["context_fingerprint"] == context_fp:
        return False
    oldest = None
    if blocking:
        oldest = prior["oldest_opened_at"] if prior and prior["blocking_count"] and prior["oldest_opened_at"] else stamp
    conn.execute("INSERT INTO ops_exception_snapshots(event_id,firm_id,company_id,blocking_count,warning_count,oldest_opened_at,context_fingerprint,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET blocking_count=excluded.blocking_count,warning_count=excluded.warning_count,oldest_opened_at=excluded.oldest_opened_at,context_fingerprint=excluded.context_fingerprint,updated_at=excluded.updated_at",
                 (event["id"], event["firm_id"], event["company_id"], blocking, warning, oldest, context_fp, stamp))
    return True


class OperationsService:
    def __init__(self, db, meter, intelligence=None):
        self.db, self.meter, self.intelligence = db, meter, intelligence
        self.access = AccessControl()

    def _company(self, conn, actor, company_id, manage=False):
        company = self.access.company(conn, actor, company_id)
        firm_role = self.access.firm_role(conn, actor)
        company_role = self.access.company_role(conn, actor, company_id)
        manager = firm_role in FIRM_MANAGERS or company_role in {"owner", "admin"}
        if not manager and company_role not in OPS_ROLES:
            raise AccountingError("企业运行信息不存在或无权访问", 404)
        if manage and not manager:
            raise AccountingError("需要企业管理员或事务所管理员权限", 403)
        return company, manager

    def companies(self, actor):
        with self.db.transaction(actor=actor) as conn:
            manager = self.access.firm_role(conn, actor) in FIRM_MANAGERS
            rows = conn.execute("SELECT c.id,c.name,m.role FROM companies c JOIN firm_clients f ON f.company_id=c.id LEFT JOIN company_memberships m ON m.company_id=c.id AND m.user_id=? WHERE f.firm_id=? AND f.status='active' AND (?=1 OR m.role IN ('owner','admin','finance')) ORDER BY c.name,c.id", (actor.user_id, actor.firm_id, int(manager)))
            return {"companies": [{"id": row["id"], "name": row["name"], "can_manage_budget": manager or row["role"] in {"owner", "admin"}} for row in rows]}

    @staticmethod
    def _policy_json(raw, manager):
        result = {key: raw[key] for key in DEFAULT_POLICY if not key.endswith("_micro") and key != "quota_enabled"}
        result["quota_enabled"] = bool(raw["quota_enabled"])
        for name in ("monthly_limit", "ocr_reserve", "proposal_reserve", "chat_reserve"):
            result[name] = cents_to_money(int(raw.get(name + "_micro", 0)) // 10_000)
        result["can_manage"] = manager
        return result

    def _policy_raw(self, conn, firm_id, company_id):
        row = conn.execute("SELECT * FROM model_company_policies WHERE firm_id=? AND company_id=?", (firm_id, company_id)).fetchone()
        return dict(row) if row else dict(DEFAULT_POLICY)

    def policy(self, actor, company_id, request=None):
        with self.db.transaction(write=request is not None, actor=actor, company_id=company_id) as conn:
            _, manager = self._company(conn, actor, company_id, manage=request is not None)
            old = self._policy_raw(conn, actor.firm_id, company_id)
            if request is None:
                return self._policy_json(old, manager)
            if request.expected_version != old["version"]:
                raise AccountingError("企业费用政策版本已更新，请刷新后重试", 409)
            raw = request.model_dump(exclude={"expected_version", "monthly_limit", "ocr_reserve", "proposal_reserve", "chat_reserve"})
            raw["quota_enabled"] = int(raw["quota_enabled"])
            for name in ("monthly_limit", "ocr_reserve", "proposal_reserve", "chat_reserve"):
                raw[name + "_micro"] = decimal_micro(getattr(request, name)) if getattr(request, name) is not None else old[name + "_micro"]
            raw.update(version=old["version"] + 1, updated_by=actor.user_id, updated_at=self.meter.timestamp(conn))
            fields = list(raw)
            if old["version"] == 0:
                conn.execute("INSERT INTO model_company_policies(firm_id,company_id," + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in range(len(fields) + 2)) + ")",
                             (actor.firm_id, company_id, *[raw[field] for field in fields]))
            else:
                updated = conn.execute("UPDATE model_company_policies SET " + ",".join(field + "=?" for field in fields) + " WHERE firm_id=? AND company_id=? AND version=?",
                                       (*[raw[field] for field in fields], actor.firm_id, company_id, old["version"]))
                if updated.rowcount != 1:
                    raise AccountingError("企业费用政策已改变，请刷新", 409)
            BusinessService._audit(conn, actor, "model.policy.update", company_id, {"version": raw["version"], "quota_enabled": bool(raw["quota_enabled"]), "monthly_limit": request.monthly_limit,
                                                                                       "ocr_reserve": request.ocr_reserve, "proposal_reserve": request.proposal_reserve, "chat_reserve": cents_to_money(raw["chat_reserve_micro"] // 10_000)})
            self._refresh_alerts(conn, actor.firm_id, company_id)
            return self._policy_json(raw, manager)

    def _metrics(self, conn, firm_id, company_id, period):
        if self.db.is_postgres:
            return _json(conn.execute("SELECT accounting.ops_company_metrics(?,?,?)", (firm_id, company_id, period)).fetchone()[0])
        policy = self._policy_raw(conn, firm_id, company_id)
        all_costs = [dict(row) for row in conn.execute("SELECT * FROM model_costs WHERE firm_id=? AND company_id=?", (firm_id, company_id))]
        current = [row for row in all_costs if row["period"] == period]
        carried = [row for row in all_costs if row["period"] < period and row["state"] in HELD_STATES]
        historical_overrun = [row for row in all_costs if row["period"] < period and row["overrun"]]
        carried_micro = sum(row["reserved_micro"] for row in carried)
        costs = {
            "run_count": len(current), "reserved_micro": sum(row["reserved_micro"] for row in current if row["state"] in HELD_STATES),
            "estimated_micro": _sum_present(current, "estimated_micro"), "confirmed_micro": _sum_present(current, "confirmed_micro"),
            "exposure_micro": self.meter.exposure(current) + carried_micro,
            "input_tokens": _sum_present(current, "input_tokens"), "output_tokens": _sum_present(current, "output_tokens"),
            "reported_count": sum(row["input_tokens"] is not None and row["output_tokens"] is not None for row in current),
            "unknown_count": sum(row["state"] == "uncertain" for row in current), "overrun_count": sum(bool(row["overrun"]) for row in current),
            "carried_reserved_micro": carried_micro, "carried_unknown_count": sum(row["state"] == "uncertain" for row in carried),
            "historical_overrun_count": len(historical_overrun),
            "risk_fingerprint": fingerprint([(row["id"], row["state"], row["version"]) for row in sorted(current + carried + historical_overrun, key=lambda row: row["id"])]),
        }
        jobs = [dict(row) for row in conn.execute("SELECT * FROM ocr_jobs WHERE firm_id=? AND company_id=?", (firm_id, company_id))]
        proposal_jobs = [dict(row) for row in conn.execute("SELECT * FROM proposal_jobs WHERE firm_id=? AND company_id=?", (firm_id, company_id))]
        waiting_count = sum(row["status"] == "waiting" for row in proposal_jobs)
        for row in proposal_jobs:
            jobs.append({**row, "id": "proposal:" + row["id"], "status": "queued" if row["status"] == "waiting" else row["status"]})
        cutoff = self.meter.clock() - timedelta(minutes=policy["failure_window_minutes"])
        queue = {state: sum(row["status"] == state for row in jobs) for state in QUEUE_STATES}
        queue.update(recent_failures=sum(row["status"] in {"failed", "interrupted"} and _date(row["finished_at"]) is not None and _date(row["finished_at"]) >= cutoff for row in jobs),
                     oldest_pending_at=min((row["created_at"] for row in jobs if row["status"] in {"queued", "retry_wait"}), default=None),
                     risk_fingerprint=fingerprint([(row["id"], row["status"], row["attempt_count"], row["finished_at"]) for row in sorted(jobs, key=lambda row: row["id"])]))
        queue.update(waiting=waiting_count, needs_attention=sum(row["status"]=="needs_attention" for row in proposal_jobs))
        snapshots = [dict(row) for row in conn.execute("SELECT x.* FROM ops_exception_snapshots x WHERE x.firm_id=? AND x.company_id=? AND EXISTS(SELECT 1 FROM ai_company_settings s WHERE s.firm_id=x.firm_id AND s.company_id=x.company_id AND s.enabled=1) AND NOT EXISTS(SELECT 1 FROM business_mappings m JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE m.event_id=x.event_id AND d.status<>'draft')", (firm_id, company_id))]
        blocked = {row["event_id"] for row in snapshots if row["blocking_count"] > 0}
        attention = conn.execute("""SELECT p.event_id,MIN(p.created_at) AS oldest_opened_at,MAX(p.updated_at) AS updated_at
            FROM proposal_jobs p JOIN drafts d ON d.id=p.draft_id AND d.book_id=p.book_id AND d.status='draft'
            JOIN ai_company_settings enabled ON enabled.firm_id=p.firm_id AND enabled.company_id=p.company_id AND enabled.enabled=1
            WHERE p.firm_id=? AND p.company_id=? AND p.status='needs_attention' GROUP BY p.event_id""",(firm_id,company_id)).fetchall()
        for row in attention:
            if row["event_id"] not in blocked:
                snapshots.append({**dict(row),"blocking_count":1,"warning_count":0,"context_fingerprint":fingerprint({"proposal_attention":row["event_id"],"updated_at":row["updated_at"]})})
        exceptions = {"blocking_events": sum(row["blocking_count"] > 0 for row in snapshots), "blocking_count": sum(row["blocking_count"] for row in snapshots), "warning_count": sum(row["warning_count"] for row in snapshots),
                      "oldest_opened_at": min((row["oldest_opened_at"] for row in snapshots if row["blocking_count"] and row["oldest_opened_at"]), default=None),
                      "updated_at": max((row["updated_at"] for row in snapshots), default=None),
                      "risk_fingerprint": fingerprint([(row["event_id"], row["context_fingerprint"], row["blocking_count"], row["warning_count"]) for row in sorted(snapshots, key=lambda row: row["event_id"])])}
        heartbeats = [dict(row) for row in conn.execute("SELECT heartbeat_at,state FROM worker_heartbeats")]
        stale_before = self.meter.clock() - timedelta(seconds=policy["worker_stale_seconds"])
        worker = {"available": any(row["state"] in {"active", "idle"} and _date(row["heartbeat_at"]) is not None and _date(row["heartbeat_at"]) >= stale_before for row in heartbeats),
                  "last_heartbeat_at": max((row["heartbeat_at"] for row in heartbeats), default=None)}
        assistant_jobs = [dict(row) for row in conn.execute("SELECT id,status,attempt_count,created_at,finished_at FROM assistant_turns WHERE firm_id=? AND company_id=?", (firm_id, company_id))]
        assistant_queue = {state: sum(row["status"] == state for row in assistant_jobs) for state in QUEUE_STATES}
        assistant_queue.update(recent_failures=sum(row["status"] in {"failed", "interrupted"} and _date(row["finished_at"]) is not None and _date(row["finished_at"]) >= cutoff for row in assistant_jobs),
                               oldest_pending_at=min((row["created_at"] for row in assistant_jobs if row["status"] == "queued"), default=None),
                               risk_fingerprint=fingerprint([(row["id"], row["status"], row["attempt_count"], row["finished_at"]) for row in sorted(assistant_jobs, key=lambda row: row["id"])]))
        assistant_heartbeats = [dict(row) for row in conn.execute("SELECT heartbeat_at,state FROM assistant_worker_heartbeats")]
        assistant_worker = {"available": any(row["state"] in {"active", "idle"} and _date(row["heartbeat_at"]) is not None and _date(row["heartbeat_at"]) >= stale_before for row in assistant_heartbeats),
                            "last_heartbeat_at": max((row["heartbeat_at"] for row in assistant_heartbeats), default=None)}
        processing_pending = queue["queued"] + queue["retry_wait"] + queue["running"]
        assistant_pending = assistant_queue["queued"] + assistant_queue["running"]
        worker["processing_available"], worker["assistant_available"] = worker["available"], assistant_worker["available"]
        worker["available"] = (not processing_pending or worker["processing_available"]) and (not assistant_pending or assistant_worker["available"]) and bool(processing_pending or assistant_pending or worker["processing_available"] or assistant_worker["available"])
        worker["last_heartbeat_at"] = max((stamp for stamp in (worker["last_heartbeat_at"], assistant_worker["last_heartbeat_at"]) if stamp), default=None)
        for state in QUEUE_STATES:
            queue[state] += assistant_queue[state]
        queue["recent_failures"] += assistant_queue["recent_failures"]
        queue["oldest_pending_at"] = min((stamp for stamp in (queue["oldest_pending_at"], assistant_queue["oldest_pending_at"]) if stamp), default=None)
        queue["risk_fingerprint"] = fingerprint({"processing": queue["risk_fingerprint"], "assistant": assistant_queue["risk_fingerprint"]})
        cases = [dict(row) for row in conn.execute("SELECT id,version,state,due_on,created_at FROM case_tasks WHERE firm_id=? AND company_id=? AND state<>'closed'", (firm_id, company_id))]
        today = self.meter.clock().astimezone(timezone(timedelta(hours=8))).date().isoformat()
        workflow = {"open_cases": len(cases), "overdue_supplements": sum(item["state"] == "awaiting_info" and item["due_on"] is not None and item["due_on"] < today for item in cases),
                    "exports_without_receipt": conn.execute("SELECT COUNT(*) FROM delivery_manifests m WHERE m.firm_id=? AND m.company_id=? AND NOT EXISTS(SELECT 1 FROM delivery_receipts r WHERE r.export_id=m.export_id)", (firm_id, company_id)).fetchone()[0],
                    "latest_receipts_unknown": 0, "latest_receipts_rejected": 0}
        for row in conn.execute("SELECT r.body_json FROM delivery_receipts r WHERE r.firm_id=? AND r.company_id=? AND r.id=(SELECT r2.id FROM delivery_receipts r2 WHERE r2.export_id=r.export_id ORDER BY r2.created_at DESC,r2.id DESC LIMIT 1)", (firm_id, company_id)):
            for item in _json(row["body_json"]).get("items", []):
                if item.get("status") == "unknown":
                    workflow["latest_receipts_unknown"] += 1
                elif item.get("status") == "rejected":
                    workflow["latest_receipts_rejected"] += 1
        exceptions["blocking_events"] += len(cases)
        exceptions["blocking_count"] += len(cases)
        exceptions["oldest_opened_at"] = min([value for value in [exceptions["oldest_opened_at"], *[item["created_at"] for item in cases]] if value], default=None)
        exceptions["risk_fingerprint"] = fingerprint({"existing": exceptions["risk_fingerprint"], "cases": [(item["id"], item["version"], item["state"]) for item in cases]})
        return {"period": period, "policy": policy, "costs": costs, "queue": queue, "exceptions": exceptions, "worker": worker, "workflow": workflow,
                "assistant": {"queue": assistant_queue, "workers": assistant_worker}}

    @staticmethod
    def _cost_json(row, manager):
        return {"id": row["id"], "kind": row["kind"], "model": row["model"], "state": row["state"], "reserved_cost": micro_decimal(row["reserved_micro"]),
                "estimated_cost": micro_decimal(row["estimated_micro"]), "confirmed_cost": micro_decimal(row["confirmed_micro"]),
                "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"], "created_at": row["created_at"], "version": row["version"],
                "reason": row["manual_reason"], "can_reconcile": bool(manager and row["state"] in {"uncertain", "estimated", "reconciled"})}

    @staticmethod
    def _alert_json(row):
        payload = _json(row["payload_json"])
        return {key: row[key] for key in ("id", "code", "severity", "state", "opened_at", "last_seen_at", "version")} | {"message": payload.get("message", "运行告警待核对")}

    def _pricing_json(self):
        settings = self.intelligence.provider.settings if self.intelligence else None
        return {kind: {"configured": rate.configured, "input_per_million": micro_decimal(decimal_micro(rate.input_per_million)) if rate.input_per_million is not None else None,
                       "output_per_million": micro_decimal(decimal_micro(rate.output_per_million)) if rate.output_per_million is not None else None,
                       "model": effective_settings(settings, {"ocr": "ocr", "proposal": "journal", "chat": "chat"}[kind]).model if settings else ""}
                for kind, rate in (("ocr", self.meter.pricing.ocr), ("proposal", self.meter.pricing.proposal), ("chat", self.meter.pricing.chat))}

    def overview(self, actor, company_id, period=None):
        if period is not None and not re.fullmatch(r"20[0-9]{2}-(?:0[1-9]|1[0-2])", period):
            raise AccountingError("费用月份须为2000至2099年的YYYY-MM", 422)
        with self.db.transaction(actor=actor, company_id=company_id) as conn:
            company, manager = self._company(conn, actor, company_id)
            period = period or self.meter.period(conn)
            metrics = self._metrics(conn, actor.firm_id, company_id, period)
            costs, raw_policy, exceptions = metrics["costs"], metrics["policy"], metrics["exceptions"]
            exposure = int(costs["exposure_micro"])
            alerts = [self._alert_json(row) for row in conn.execute("SELECT * FROM ops_alerts WHERE firm_id=? AND company_id=? ORDER BY CASE state WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,opened_at DESC,id", (actor.firm_id, company_id))]
            records = [self._cost_json(row, manager) for row in conn.execute("SELECT * FROM model_costs WHERE firm_id=? AND company_id=? AND (period=? OR (period<? AND (state IN ('reserved','sent','uncertain') OR overrun=1))) ORDER BY CASE WHEN overrun=1 THEN 0 WHEN state='uncertain' THEN 1 WHEN state IN ('reserved','sent') THEN 2 ELSE 3 END,created_at DESC,id DESC LIMIT 100", (actor.firm_id, company_id, period, period))]
            queue = {key: value for key, value in metrics["queue"].items() if key != "risk_fingerprint"}
            return {"company": {"id": company["id"], "name": company["name"]}, "period": period, "currency": "CNY",
                    "policy": self._policy_json(raw_policy, manager), "pricing": self._pricing_json(),
                    "usage": {"requests": int(costs["run_count"]), "input_tokens": int(costs["input_tokens"]) if costs.get("input_tokens") is not None else None,
                              "output_tokens": int(costs["output_tokens"]) if costs.get("output_tokens") is not None else None,
                              "reported_requests": int(costs["reported_count"]), "uncertain_requests": int(costs["unknown_count"]) + int(costs.get("carried_unknown_count") or 0),
                              "reserved_cost": micro_decimal(int(costs["reserved_micro"])), "carried_reserved_cost": micro_decimal(int(costs.get("carried_reserved_micro") or 0)),
                              "estimated_cost": micro_decimal(int(costs["estimated_micro"])) if costs["estimated_micro"] is not None else None,
                              "confirmed_cost": micro_decimal(int(costs["confirmed_micro"])) if costs["confirmed_micro"] is not None else None,
                              "exposure": micro_decimal(exposure), "available": micro_decimal(max(0, int(raw_policy["monthly_limit_micro"]) - exposure)) if raw_policy["quota_enabled"] else None,
                              "overrun_count": int(costs["overrun_count"]) + int(costs.get("historical_overrun_count") or 0)},
                    "queue": queue, "exceptions": {"events": int(exceptions["blocking_events"]), "blocking": int(exceptions["blocking_count"]), "warning": int(exceptions["warning_count"]),
                                                       "oldest_opened_at": exceptions["oldest_opened_at"], "updated_at": exceptions.get("updated_at")},
                    "workers": {"available": bool(metrics["worker"]["available"]), "last_heartbeat_at": metrics["worker"]["last_heartbeat_at"],
                                "processing_available": bool(metrics["worker"].get("processing_available", metrics["worker"]["available"])), "assistant_available": bool(metrics["worker"].get("assistant_available", False))},
                    "assistant": {"queue": {key: value for key, value in metrics.get("assistant", {}).get("queue", {}).items() if key != "risk_fingerprint"},
                                  "workers": metrics.get("assistant", {}).get("workers", {"available": False, "last_heartbeat_at": None})},
                    "alerts": alerts, "costs": records, "can_manage_budget": manager,
                    "workflow": metrics.get("workflow", {}), "monitor": self._monitor_state(conn, actor.firm_id, company_id)}

    @staticmethod
    def _monitor_state(conn, firm_id, company_id):
        row = conn.execute("SELECT last_refreshed_at,next_due_at FROM ops_company_refresh WHERE firm_id=? AND company_id=?", (firm_id, company_id)).fetchone()
        return dict(row) if row else {"last_refreshed_at": None, "next_due_at": None}

    def _refresh_alerts(self, conn, firm_id, company_id):
        if self.db.is_postgres:
            return conn.execute("SELECT accounting.ops_refresh_company_alerts(?,?)", (firm_id, company_id)).fetchone()[0]
        # The caller has verified operations scope, or is the trusted local
        # dispatcher. Only that company's reservations change here.
        cutoff = (self.meter.clock() - timedelta(seconds=180)).astimezone(timezone.utc).isoformat()
        conn.execute("UPDATE model_costs SET state='uncertain',finished_at=?,version=version+1 WHERE firm_id=? AND company_id=? AND state IN ('reserved','sent') AND COALESCE(sent_at,created_at)<=?", (self.meter.timestamp(conn), firm_id, company_id, cutoff))
        metrics = self._metrics(conn, firm_id, company_id, self.meter.period(conn))
        policy, costs, queue, exceptions, worker = (metrics[name] for name in ("policy", "costs", "queue", "exceptions", "worker"))
        clock = self.meter.clock()
        old_exception = _date(exceptions["oldest_opened_at"])
        old_queue = _date(queue["oldest_pending_at"])
        pending = queue["queued"] + queue["retry_wait"]
        unknown = costs["unknown_count"] + costs.get("carried_unknown_count", 0)
        overrun = costs["overrun_count"] + costs.get("historical_overrun_count", 0)
        conditions = {
            "budget_exhausted": (bool(policy["quota_enabled"] and costs["exposure_micro"] >= policy["monthly_limit_micro"]), costs["exposure_micro"], "本月模型费用额度已用尽", "critical", costs["risk_fingerprint"]),
            "quota_overrun": (overrun > 0, overrun, "模型费用超过单次预留金额，须核对成本", "critical", costs["risk_fingerprint"]),
            "usage_uncertain": (unknown > 0, unknown, "部分模型调用的用量或费用尚未确认", "warning", costs["risk_fingerprint"]),
            "task_failures": (queue["recent_failures"] >= policy["failure_threshold"], queue["recent_failures"], "近期后台处理失败较多，请检查服务", "warning", queue["risk_fingerprint"]),
            "exception_backlog": (bool(exceptions["blocking_events"] >= policy["backlog_threshold"] and old_exception and old_exception <= clock - timedelta(minutes=policy["backlog_age_minutes"])), exceptions["blocking_events"], "会计核对异常积压，需要人工处理", "warning", exceptions["risk_fingerprint"]),
            "queue_wait": (bool(pending and old_queue and old_queue <= clock - timedelta(minutes=policy["queue_wait_minutes"])), pending, "后台任务等待时间过长", "warning", queue["risk_fingerprint"]),
            "worker_unavailable": (bool(pending + queue["running"] > 0 and not worker["available"]), pending + queue["running"], "有待处理任务，但后台进程心跳不可用", "critical", queue["risk_fingerprint"]),
        }
        stamp = self.meter.timestamp(conn)
        for code, (triggered, count, message, severity, risk) in conditions.items():
            prior = conn.execute("SELECT * FROM ops_alerts WHERE firm_id=? AND company_id=? AND code=?", (firm_id, company_id, code)).fetchone()
            if not triggered:
                if prior and prior["state"] != "resolved":
                    conn.execute("UPDATE ops_alerts SET state='resolved',resolved_at=?,last_seen_at=?,version=version+1 WHERE id=?", (stamp, stamp, prior["id"]))
                continue
            payload = {"message": message, "count": count}
            digest = fingerprint({"code": code, "payload": payload, "risk": risk, "policy_version": policy["version"]})
            if prior:
                reopen = prior["state"] == "resolved" or prior["state"] == "acknowledged" and prior["fingerprint"] != digest
                next_state = "open" if reopen else prior["state"]
                revision = int(prior["fingerprint"] != digest or prior["state"] != next_state or prior["severity"] != severity)
                conn.execute("UPDATE ops_alerts SET state=?,severity=?,fingerprint=?,payload_json=?,opened_at=?,last_seen_at=?,resolved_at=NULL,version=version+? WHERE id=?",
                             (next_state, severity, digest, encode(payload), stamp if prior["state"] == "resolved" else prior["opened_at"], stamp, revision, prior["id"]))
            else:
                conn.execute("INSERT INTO ops_alerts(id,firm_id,company_id,code,severity,state,fingerprint,payload_json,opened_at,last_seen_at,version) VALUES(?,?,?,?,?,'open',?,?,?,?,1)", (uuid4().hex, firm_id, company_id, code, severity, digest, encode(payload), stamp, stamp))
        return {"refreshed": 7}

    def refresh(self, actor, company_id):
        with self.db.transaction(write=True, actor=actor, company_id=company_id) as conn:
            self._company(conn, actor, company_id)
            self._refresh_alerts(conn, actor.firm_id, company_id)
        return self.overview(actor, company_id)

    def acknowledge(self, actor, alert_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = conn.execute("SELECT * FROM ops_alerts WHERE id=? AND firm_id=?", (alert_id, actor.firm_id)).fetchone()
            if not row:
                raise AccountingError("运行告警不存在或无权访问", 404)
            self._company(conn, actor, row["company_id"])
            if request.expected_version != row["version"] or row["state"] == "resolved":
                raise AccountingError("告警版本或状态已更新，请刷新", 409)
            changed = conn.execute("UPDATE ops_alerts SET state='acknowledged',version=version+1 WHERE id=? AND version=? AND state<>'resolved'", (alert_id, request.expected_version))
            if changed.rowcount != 1:
                raise AccountingError("告警状态已更新，请刷新", 409)
            conn.execute("INSERT INTO ops_alert_actions(id,alert_id,firm_id,company_id,created_by,reason,created_at) VALUES(?,?,?,?,?,?,?)", (uuid4().hex, alert_id, actor.firm_id, row["company_id"], actor.user_id, request.reason, self.meter.timestamp(conn)))
            BusinessService._audit(conn, actor, "operations.alert.acknowledge", alert_id, {"reason": request.reason, "version": row["version"] + 1})
            return self._alert_json(conn.execute("SELECT * FROM ops_alerts WHERE id=?", (alert_id,)).fetchone())

    def reconcile(self, actor, cost_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = conn.execute("SELECT * FROM model_costs WHERE id=? AND firm_id=?", (cost_id, actor.firm_id)).fetchone()
            if not row:
                raise AccountingError("模型费用记录不存在或无权访问", 404)
            self._company(conn, actor, row["company_id"], manage=True)
            if request.expected_version != row["version"] or row["state"] not in {"uncertain", "estimated", "reconciled"}:
                raise AccountingError("费用版本或状态已更新，执行中的费用不能核定", 409)
            amount = decimal_micro(request.confirmed_cost)
            if self.db.is_postgres:
                valid = conn.execute("SELECT accounting.manual_reconcile_model_cost(?,?,?,?)", (cost_id, request.expected_version, amount, request.reason)).fetchone()[0]
                if not valid:
                    raise AccountingError("费用核定未通过，请刷新当前版本与授权", 409)
            else:
                changed = conn.execute("UPDATE model_costs SET confirmed_micro=?,state='reconciled',overrun=0,manual_reason=?,manual_by=?,version=version+1 WHERE id=? AND version=?", (amount, request.reason, actor.user_id, cost_id, request.expected_version))
                if changed.rowcount != 1:
                    raise AccountingError("费用版本已更新，请刷新", 409)
                BusinessService._audit(conn, actor, "model.cost.reconcile", cost_id, {"previous_confirmed_micro": row["confirmed_micro"], "confirmed_micro": amount, "reason": request.reason, "version": row["version"] + 1})
            self._refresh_alerts(conn, actor.firm_id, row["company_id"])
            return self._cost_json(conn.execute("SELECT * FROM model_costs WHERE id=?", (cost_id,)).fetchone(), True)

    def heartbeat(self, conn, worker_id, inflight=0, state="idle"):
        """Heartbeat is callable in its own short transaction, without scanning."""
        if state not in {"active", "idle", "stopped"} or type(inflight) is not int or not 0 <= inflight <= 2 or (state != "active" and inflight):
            raise ValueError("后台心跳状态不合法")
        if not isinstance(worker_id, str) or not 1 <= len(worker_id.strip()) <= 120:
            raise ValueError("后台标识不合法")
        if self.db.is_postgres:
            return conn.execute("SELECT accounting.record_worker_heartbeat(?,?,?)", (worker_id, state, inflight)).fetchone()[0]
        stamp = self.meter.timestamp(conn)
        conn.execute("INSERT INTO worker_heartbeats(worker_id,started_at,heartbeat_at,state,inflight_count) VALUES(?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET started_at=CASE WHEN worker_heartbeats.state='stopped' AND excluded.state<>'stopped' THEN excluded.started_at ELSE worker_heartbeats.started_at END,heartbeat_at=excluded.heartbeat_at,state=excluded.state,inflight_count=excluded.inflight_count", (worker_id, stamp, stamp, state, inflight))
        return True

    def refresh_pending(self, conn, batch_limit=20, deadline_limit=5):
        """Worker-only bounded fair queue, plus periodic time-based rechecks."""
        if type(batch_limit) is not int or not 1 <= batch_limit <= 100 or type(deadline_limit) is not int or not 0 <= deadline_limit <= 20:
            raise ValueError("监控批量参数不合法")
        if self.db.is_postgres:
            return conn.execute("SELECT accounting.ops_refresh_dirty(?,?)", (batch_limit, deadline_limit)).fetchone()[0]
        stamp, refreshed = self.meter.timestamp(conn), 0
        next_due = (self.meter.clock() + timedelta(seconds=30)).astimezone(timezone.utc).isoformat()
        dirty = conn.execute("SELECT d.firm_id,d.company_id FROM ops_company_dirty d JOIN firm_clients f USING(firm_id,company_id) WHERE f.status='active' ORDER BY d.dirty_at,d.firm_id,d.company_id LIMIT ?", (batch_limit,)).fetchall()
        for company in dirty:
            self._refresh_alerts(conn, company["firm_id"], company["company_id"])
            conn.execute("DELETE FROM ops_company_dirty WHERE firm_id=? AND company_id=?", (company["firm_id"], company["company_id"]))
            conn.execute("UPDATE ops_company_refresh SET last_refreshed_at=?,next_due_at=? WHERE firm_id=? AND company_id=?", (stamp, next_due, company["firm_id"], company["company_id"]))
            refreshed += 1
        due = conn.execute("SELECT r.firm_id,r.company_id FROM ops_company_refresh r JOIN firm_clients f USING(firm_id,company_id) WHERE f.status='active' AND r.next_due_at<=? ORDER BY r.next_due_at,r.firm_id,r.company_id LIMIT ?", (stamp, deadline_limit)).fetchall()
        for company in due:
            self._refresh_alerts(conn, company["firm_id"], company["company_id"])
            conn.execute("DELETE FROM ops_company_dirty WHERE firm_id=? AND company_id=?", (company["firm_id"], company["company_id"]))
            conn.execute("UPDATE ops_company_refresh SET last_refreshed_at=?,next_due_at=? WHERE firm_id=? AND company_id=?", (stamp, next_due, company["firm_id"], company["company_id"]))
            refreshed += 1
        return refreshed

    def maintenance(self, conn, worker_id, inflight=0, state="idle", batch_limit=20, deadline_limit=5, update_heartbeat=True):
        """Compatibility entry point; workers can commit heartbeat separately."""
        if update_heartbeat:
            self.heartbeat(conn, worker_id, inflight, state)
        return self.refresh_pending(conn, batch_limit, deadline_limit)
