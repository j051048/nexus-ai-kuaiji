from datetime import datetime, timedelta, timezone
import json
import re

from ..accounting.domain import AccountingError
from ..workspace.projection import status_snapshot


class FirmObservabilityService:
    def __init__(self, db, accounting):
        self.db, self.access = db, accounting.access

    @staticmethod
    def _counts(conn, table, firm_id, statuses):
        if not statuses:
            return {}
        placeholders = ",".join("?" for _ in statuses)
        return {
            row["status"]: row["count"]
            for row in conn.execute(
                f"SELECT status,COUNT(*) AS count FROM {table} WHERE firm_id=? "
                f"AND status IN({placeholders}) GROUP BY status",
                (firm_id, *statuses),
            )
        }

    def _worker_health(self, conn):
        """Expose shared service availability, never global worker identities/load.

        snapshot has already checked the current firm manager. PostgreSQL also
        repeats that check inside a narrow, read-only SECURITY DEFINER function;
        the runtime role deliberately has no worker_heartbeats SELECT grant.
        """
        if self.db.is_postgres:
            result = conn.execute("SELECT accounting.firm_worker_health()").fetchone()[0]
            return json.loads(result) if isinstance(result, str) else result
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        row = conn.execute(
            "SELECT COALESCE(MAX(CASE WHEN state IN ('active','idle') "
            "AND julianday(heartbeat_at)>=julianday(?) THEN 1 ELSE 0 END),0) AS available,"
            "MAX(heartbeat_at) AS last_heartbeat_at FROM worker_heartbeats",
            (cutoff,),
        ).fetchone()
        return {
            "available": bool(row["available"]),
            "last_heartbeat_at": row["last_heartbeat_at"],
            "stale_after_seconds": 90,
        }

    def snapshot(self, actor, period=""):
        if not isinstance(period, str) or (
            period and not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", period)
        ):
            raise AccountingError("请选择有效的 YYYY-MM 月份", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            queues = {
                "ocr": self._counts(
                    conn,
                    "ocr_jobs",
                    actor.firm_id,
                    ("queued", "running", "retry_wait", "failed", "interrupted", "stale"),
                ),
                "proposal": self._counts(
                    conn,
                    "proposal_jobs",
                    actor.firm_id,
                    ("waiting", "queued", "running", "retry_wait", "needs_attention", "failed"),
                ),
                "migration": self._counts(
                    conn,
                    "migration_jobs",
                    actor.firm_id,
                    ("queued", "running", "retry_wait", "failed", "stale"),
                ),
                "batch": self._counts(
                    conn,
                    "batch_operation_items",
                    actor.firm_id,
                    ("queued", "running", "retry_wait", "failed"),
                ),
            }
            alerts = {
                row["code"]: {"open": row["open"], "critical": row["critical"]}
                for row in conn.execute(
                    "SELECT code,SUM(CASE WHEN state<>'resolved' THEN 1 ELSE 0 END) AS open,"
                    "SUM(CASE WHEN state<>'resolved' AND severity='critical' THEN 1 ELSE 0 END) "
                    "AS critical FROM ops_alerts WHERE firm_id=? GROUP BY code",
                    (actor.firm_id,),
                )
            }
            worker_health = self._worker_health(conn)
            minute = int(datetime.now(timezone.utc).timestamp() // 60)
            metrics = conn.execute(
                "SELECT COUNT(*) AS buckets,COALESCE(SUM(tx_count),0) AS transactions,"
                "COALESCE(SUM(write_count),0) AS writes,"
                "COALESCE(SUM(rollback_count),0) AS rollbacks,"
                "SUM(connection_sum)*1.0/NULLIF(SUM(tx_count),0) AS avg_connection_ms,"
                "SUM(lock_sum)*1.0/NULLIF(SUM(tx_count),0) AS avg_lock_ms,"
                "MAX(transaction_max) AS max_transaction_ms "
                "FROM transaction_metric_buckets WHERE firm_id=? AND bucket_minute>=?",
                (actor.firm_id, minute - 60),
            ).fetchone()
            cost_period = period or datetime.now(timezone.utc).strftime("%Y-%m")
            costs = conn.execute(
                "SELECT COUNT(*) AS sample_count,"
                "SUM(CASE WHEN state='released' THEN 0 ELSE COALESCE(confirmed_micro,estimated_micro) END) AS micros,"
                "SUM(CASE WHEN state<>'released' AND confirmed_micro IS NULL AND estimated_micro IS NULL THEN 1 ELSE 0 END) AS unknown_count,"
                "SUM(CASE WHEN state<>'released' AND confirmed_micro IS NULL AND estimated_micro IS NOT NULL THEN 1 ELSE 0 END) AS estimated_count "
                "FROM model_costs WHERE firm_id=? AND period=?",
                (actor.firm_id, cost_period),
            ).fetchone()
            projection = status_snapshot(conn, actor.firm_id)
            cost_summary = {
                "sample_count": int(costs["sample_count"]),
                "known_micro": int(costs["micros"]) if costs["micros"] is not None else None,
                "unknown_count": int(costs["unknown_count"] or 0),
                "estimated_count": int(costs["estimated_count"] or 0),
            }
            signals = []

            def signal(code, text, target):
                signals.append(
                    {
                        "code": code,
                        "owner_role": "admin",
                        "next_action": text,
                        "target": target,
                        "automatic_action": False,
                    }
                )

            if not worker_health["available"]:
                signal(
                    "worker_heartbeat_missing",
                    "请管理员检查后台服务及最近日志；先核对在途任务，不直接重发。",
                    "queue",
                )
            if cost_summary["unknown_count"]:
                signal(
                    "cost_unknown",
                    "请管理员按调用记录核定费用；未知结果不得当作免费或自动重发。",
                    "operations",
                )
            if any(
                sum(counts.get(s, 0) for s in ("failed", "interrupted", "stale", "needs_attention"))
                for counts in queues.values()
            ):
                signal(
                    "task_attention", "打开处理任务，核对原始失败原因后交给对应负责人。", "queue"
                )
            if projection["failing"]:
                signal(
                    "projection_failing",
                    "请管理员核对汇总任务日志；以原记录为准，不把旧汇总当实时状态。",
                    "runs",
                )
        return {
            "firm_id": actor.firm_id,
            "period": cost_period,
            "cost_summary": cost_summary,
            "signals": signals,
            "queues": queues,
            "alerts": alerts,
            "worker_health": worker_health,
            # Deprecated empty field keeps already-open clients compatible.
            # Never repopulate this with global worker identities or workload.
            "workers": [],
            "metrics": dict(metrics),
            "ai_cost_cny": round((costs["micros"] or 0) / 1_000_000, 2),
            "projection": projection,
            "trace_hint": "每个 API 响应包含 X-Request-Id，可与结构化日志和任务记录关联。",
        }
