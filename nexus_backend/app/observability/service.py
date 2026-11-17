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

    def _operations(self, conn, firm_id):
        age_sql = (
            "SELECT MAX(CAST(EXTRACT(EPOCH FROM (now()-created_at::timestamptz)) AS BIGINT)) "
            "FROM {table} WHERE firm_id=? AND status IN ({statuses})"
            if self.db.is_postgres
            else "SELECT MAX(CAST((julianday('now')-julianday(created_at))*86400 AS INTEGER)) "
            "FROM {table} WHERE firm_id=? AND status IN ({statuses})"
        )
        ages = []
        for table, statuses in (
            ("ocr_jobs", ("queued", "retry_wait", "failed", "interrupted", "stale")),
            ("proposal_jobs", ("waiting", "queued", "retry_wait", "needs_attention", "failed")),
            ("batch_operation_items", ("queued", "retry_wait", "failed")),
        ):
            row = conn.execute(
                age_sql.format(table=table, statuses=",".join("?" for _ in statuses)),
                (firm_id, *statuses),
            ).fetchone()
            if row and row[0] is not None:
                ages.append(int(row[0]))
        material = conn.execute(
            "SELECT COUNT(*) AS count FROM material_requests r WHERE r.firm_id=? "
            "AND NOT EXISTS(SELECT 1 FROM material_request_actions a "
            "WHERE a.request_id=r.id AND a.state='accepted')",
            (firm_id,),
        ).fetchone()["count"]
        drafts = conn.execute(
            "SELECT COUNT(*) AS count FROM drafts d JOIN books b ON b.id=d.book_id "
            "WHERE b.firm_id=? AND d.status='draft'",
            (firm_id,),
        ).fetchone()["count"]
        rejections = 0
        for table, batch in (
            ("tax_fee_batch_rows", "tax_fee_batches"),
            ("tax_cost_batch_rows", "tax_cost_batches"),
        ):
            rejections += int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} r JOIN {batch} b ON b.id=r.batch_id "
                    "WHERE b.firm_id=? AND r.status='rejected'",
                    (firm_id,),
                ).fetchone()["count"]
            )
        unknown = conn.execute(
            "SELECT COUNT(*) AS count FROM delivery_notifications "
            "WHERE firm_id=? AND state='unknown'",
            (firm_id,),
        ).fetchone()["count"]
        return {
            "materials_pending": int(material),
            "drafts_pending": int(drafts),
            "source_rejections": rejections,
            "notification_unknown": int(unknown),
            "oldest_queue_age_seconds": max(ages) if ages else 0,
        }

    @staticmethod
    def _sla(operations):
        thresholds = {
            "queue_age_seconds": 900,
            "source_rejections": 0,
            "notification_unknown": 0,
        }
        breaches = []
        if int(operations.get("oldest_queue_age_seconds") or 0) > thresholds["queue_age_seconds"]:
            breaches.append(
                {"code": "queue_age", "count": int(operations["oldest_queue_age_seconds"])}
            )
        if int(operations.get("source_rejections") or 0) > thresholds["source_rejections"]:
            breaches.append(
                {"code": "source_rejections", "count": int(operations["source_rejections"])}
            )
        if int(operations.get("notification_unknown") or 0) > thresholds["notification_unknown"]:
            breaches.append(
                {"code": "notification_unknown", "count": int(operations["notification_unknown"])}
            )
        return {"thresholds": thresholds, "breaches": breaches, "automatic_action": False}

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
            operations = self._operations(conn, actor.firm_id)
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
            if operations["materials_pending"]:
                signal(
                    "material_backlog",
                    "打开月度工作台，按企业与期间核对未验收资料；不自动视为资料齐全。",
                    "workbench",
                )
            if operations["drafts_pending"]:
                signal(
                    "review_backlog",
                    "打开待复核凭证，按当前版本完成独立复核；积压不等于可以合并审批。",
                    "workbench",
                )
            if operations["source_rejections"]:
                signal(
                    "source_rejections",
                    "打开税务底稿，核对规费/成本批次拒绝行与源行理由；不得直接改为采用。",
                    "operations",
                )
            if operations["notification_unknown"]:
                signal(
                    "notification_unknown",
                    "打开人工接手，核对 unknown 发送；先查既有结果，不自动重发。",
                    "operations",
                )
            if (operations["oldest_queue_age_seconds"] or 0) >= 3600:
                signal(
                    "queue_age",
                    "打开任务队列，按等待时间核查卡住的领取与租约；不要盲目重启后重发。",
                    "queue",
                )
        return {
            "firm_id": actor.firm_id,
            "period": cost_period,
            "cost_summary": cost_summary,
            "signals": signals,
            "queues": queues,
            "operations": operations,
            "sla": self._sla(operations),
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
