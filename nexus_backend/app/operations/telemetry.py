"""Bounded cross-process observations under current company manager ACL."""

from datetime import datetime, timedelta, timezone
from time import time

from .service import OperationsService


class TelemetryService:
    def __init__(self, db, operations: OperationsService):
        self.db, self.operations = db, operations

    def overview(self, actor, company_id):
        with self.db.transaction(actor=actor, company_id=company_id) as conn:
            self.operations._company(conn, actor, company_id, manage=True)
            shared = self._shared(conn, actor, company_id)
            runtime = self._queue(conn, actor, company_id)
        metrics = self.db.metrics.snapshot(firm_id=actor.firm_id, company_id=company_id)
        return {
            "company_id": company_id,
            "transactions": metrics,
            "shared_transactions": shared,
            "queue_runtime": runtime,
            "pool": self.db.pool_snapshot(),
            "limits": {"global_model_concurrency": 2, "company_model_concurrency": 1},
            "measurement": "事务分桶汇总 API 与各 worker 的已标记企业事务，约5秒刷新；排队及执行耗时来自持久任务时间。未标记企业、调度器全局事务不归属企业；样本达到上限时吞吐为下限。连接池指标仅当前进程。",
        }

    def _shared(self, conn, actor, company_id):
        since = int(time() // 60) - 14
        row = conn.execute(
            "SELECT COALESCE(SUM(tx_count),0) AS samples,COALESCE(SUM(write_count),0) AS writes,"
            "COALESCE(SUM(rollback_count),0) AS rollbacks,SUM(connection_sum) AS connection_sum,"
            "SUM(lock_sum) AS lock_sum,SUM(transaction_sum) AS transaction_sum,"
            "MAX(connection_max) AS connection_max,MAX(lock_max) AS lock_max,"
            "MAX(transaction_max) AS transaction_max,COUNT(DISTINCT stream_id) AS streams,"
            "MAX(bucket_minute) AS newest_minute FROM transaction_metric_buckets "
            "WHERE firm_id=? AND company_id=? AND bucket_minute>=?",
            (actor.firm_id, company_id, since),
        ).fetchone()

        def stats(prefix):
            return {
                "mean_ms": round(float(row[prefix + "_sum"]) / row["samples"], 3)
                if row["samples"]
                else None,
                "max_ms": round(float(row[prefix + "_max"]), 3) if row["samples"] else None,
            }

        return {
            "scope": "all_processes",
            "window_seconds": 900,
            "bucket_seconds": 60,
            "flush_seconds": 5,
            "count": row["samples"],
            "writes": row["writes"],
            "rollbacks": row["rollbacks"],
            "streams": row["streams"],
            "newest_bucket_minute": row["newest_minute"],
            "connection": stats("connection"),
            "lock_wait": stats("lock"),
            "transaction": stats("transaction"),
        }

    def _queue(self, conn, actor, company_id):
        since = (datetime.now(timezone.utc) - timedelta(seconds=900)).isoformat()
        maximum = 500
        if self.db.is_postgres:
            rows = conn.execute(
                "SELECT accounting.company_runtime_samples(?,?,?)", (company_id, since, maximum)
            ).fetchone()[0]
        else:
            rows = []
            for kind in ("ocr", "proposal"):
                rows.extend(
                    dict(row) | {"kind": kind}
                    for row in conn.execute(
                        "SELECT a.attempt_no,a.status,a.created_at AS started_at,a.finished_at,j.created_at AS queued_at "
                        f"FROM {kind}_attempts a JOIN {kind}_jobs j ON j.id=a.job_id "
                        "WHERE a.firm_id=? AND a.company_id=? AND a.created_at>=? "
                        "ORDER BY a.created_at DESC,a.id DESC LIMIT ?",
                        (actor.firm_id, company_id, since, maximum),
                    )
                )
        result = []
        for kind in ("ocr", "proposal"):
            samples = [row for row in rows if row["kind"] == kind]
            waits, durations = [], []
            for row in samples:
                if row["attempt_no"] == 1:
                    waits.append(self._elapsed(row["queued_at"], row["started_at"]))
                if row["finished_at"]:
                    durations.append(self._elapsed(row["started_at"], row["finished_at"]))
            succeeded = sum(row["status"] == "succeeded" for row in samples)
            result.append(
                {
                    "kind": kind,
                    "attempts": len(samples),
                    "completed_attempts": len(durations),
                    "succeeded_attempts": succeeded,
                    "failed_attempts": sum(
                        row["status"] in {"failed", "interrupted", "stale"} for row in samples
                    ),
                    "queue_wait": self._stats(waits),
                    "processing": self._stats(durations),
                    "completed_per_minute": round(len(durations) / 15, 3),
                    "sample_limit_reached": len(samples) >= maximum,
                }
            )
        return {
            "scope": "all_processes",
            "window_seconds": 900,
            "sample_limit_per_kind": maximum,
            "kinds": result,
            "notes": "近期领取的票据/草稿任务尝试；排队只统计首次领取，重试不重复计算首次排队；处理包含已结束失败尝试。达到样本上限时是有限样本，不能当作全量吞吐。助手及旧账迁移尚无独立领取时间，不混入耗时。",
        }

    @staticmethod
    def _elapsed(start, finish):
        try:
            return max(
                0,
                (datetime.fromisoformat(finish) - datetime.fromisoformat(start)).total_seconds()
                * 1000,
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _stats(values):
        values = sorted(value for value in values if value is not None)
        return {
            "samples": len(values),
            "mean_ms": round(sum(values) / len(values), 3) if values else None,
            "p95_ms": round(values[max(0, (95 * len(values) + 99) // 100 - 1)], 3)
            if values
            else None,
            "max_ms": round(values[-1], 3) if values else None,
        }
