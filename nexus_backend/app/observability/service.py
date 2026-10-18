from datetime import datetime, timedelta, timezone

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

    def snapshot(self, actor, period=""):
        if period and not (
            len(period) == 7
            and period[4] == "-"
            and period[:4].isdigit()
            and 1 <= int(period[5:]) <= 12
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
            worker_rows = conn.execute(
                "SELECT worker_id,state,heartbeat_at,inflight_count FROM worker_heartbeats "
                "ORDER BY heartbeat_at DESC LIMIT 20"
            ).fetchall()
            minute = int(datetime.now(timezone.utc).timestamp() // 60)
            metrics = conn.execute(
                "SELECT COUNT(*) AS buckets,COALESCE(SUM(tx_count),0) AS transactions,"
                "COALESCE(SUM(write_count),0) AS writes,"
                "COALESCE(SUM(rollback_count),0) AS rollbacks,"
                "COALESCE(AVG(connection_sum/NULLIF(tx_count,0)),0) AS avg_connection_ms,"
                "COALESCE(AVG(lock_sum/NULLIF(tx_count,0)),0) AS avg_lock_ms,"
                "COALESCE(MAX(transaction_max),0) AS max_transaction_ms "
                "FROM transaction_metric_buckets WHERE firm_id=? AND bucket_minute>=?",
                (actor.firm_id, minute - 60),
            ).fetchone()
            cost_period = period or datetime.now(timezone.utc).strftime("%Y-%m")
            costs = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(confirmed_micro,estimated_micro,0)),0) AS micros "
                "FROM model_costs WHERE firm_id=? AND period=?",
                (actor.firm_id, cost_period),
            ).fetchone()
            projection = status_snapshot(conn, actor.firm_id)
        return {
            "queues": queues,
            "alerts": alerts,
            "workers": [dict(row) for row in worker_rows],
            "metrics": dict(metrics),
            "ai_cost_cny": round((costs["micros"] or 0) / 1_000_000, 2),
            "projection": projection,
            "trace_hint": "每个 API 响应包含 X-Request-Id，可与结构化日志和任务记录关联。",
        }
