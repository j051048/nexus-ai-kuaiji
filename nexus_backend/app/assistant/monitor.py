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
            if self.db.is_postgres:
                conn.begin(write=True, role="accounting_worker")
            else:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat(self, inflight=0, state="idle"):
        """Persist liveness only; never run alert refresh on a task thread."""
        if (
            state not in {"active", "idle", "stopped"}
            or type(inflight) is not int
            or not 0 <= inflight <= 2
            or (state != "active" and inflight != 0)
        ):
            raise ValueError("助手心跳状态或运行数量不合法")
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

    def tick(self, inflight=0, state="idle"):
        """Compatibility entry: liveness first, then bounded alert refresh."""
        self.heartbeat(inflight, state)
        return self.refresh()

    def refresh(self):
        """Bounded alert maintenance; independent of the liveness writer."""
        with self._transaction() as conn:
            return self.operations.refresh_pending(conn)
