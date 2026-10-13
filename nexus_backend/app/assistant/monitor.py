"""Dedicated dialogue heartbeat plus the existing bounded alert refresh queue."""

from contextlib import contextmanager

from app.operations.meter import MeterService
from app.operations.service import OperationsService


class AssistantMonitor:
    def __init__(self, db, worker_id, operations=None):
        if not isinstance(worker_id, str) or not 1 <= len(worker_id.strip()) <= 120:
            raise ValueError("助手后台标识须为 1–120 字")
        self.db, self.worker_id = db, worker_id
        self.operations = operations or OperationsService(db, MeterService(db))

    @contextmanager
    def _transaction(self):
        conn = self.db.connect()
        try:
            conn.execute("BEGIN" if self.db.is_postgres else "BEGIN IMMEDIATE")
            if self.db.is_postgres:
                conn.execute("SET LOCAL ROLE accounting_worker")
                conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def tick(self, inflight=0, state="idle"):
        if (
            state not in {"active", "idle", "stopped"}
            or type(inflight) is not int
            or not 0 <= inflight <= 2
            or (state != "active" and inflight != 0)
        ):
            raise ValueError("助手心跳状态或运行数量不合法")
        # A failed alert refresh cannot roll back the liveness observation.
        with self._transaction() as conn:
            if self.db.is_postgres:
                valid = conn.execute(
                    "SELECT accounting.record_assistant_heartbeat(?,?,?)",
                    (self.worker_id, state, inflight),
                ).fetchone()[0]
                if not valid:
                    raise ValueError("助手心跳未通过独立后台权限检查")
            else:
                stamp = self.operations.meter.timestamp(conn)
                conn.execute(
                    "INSERT INTO assistant_worker_heartbeats(worker_id,started_at,heartbeat_at,state,inflight_count) VALUES(?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET started_at=CASE WHEN assistant_worker_heartbeats.state='stopped' AND excluded.state<>'stopped' THEN excluded.started_at ELSE assistant_worker_heartbeats.started_at END,heartbeat_at=excluded.heartbeat_at,state=excluded.state,inflight_count=excluded.inflight_count",
                    (self.worker_id, stamp, stamp, state, inflight),
                )
        with self._transaction() as conn:
            return self.operations.refresh_pending(conn)
