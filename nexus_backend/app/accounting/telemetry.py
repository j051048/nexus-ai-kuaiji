"""Bounded process-local diagnostics. No SQL, credentials or business payloads."""
from collections import deque
from threading import Lock
from time import monotonic
from contextvars import ContextVar

transaction_scope = ContextVar('accounting_transaction_scope', default=None)


def tag_authorized_company(company_id):
    scope = transaction_scope.get()
    if scope is not None:
        scope.add(company_id)


class TransactionMetrics:
    def __init__(self, capacity=4096, window_seconds=900):
        self._events = deque(maxlen=capacity)
        self._lock = Lock()
        self.window_seconds = window_seconds

    def record(self, *, firm_id, company_id=None, write=False, outcome="committed",
               connect_ms=0, lock_wait_ms=0, transaction_ms=0):
        if not firm_id:
            return  # System/worker-wide operations cannot be attributed to a tenant.
        event = (monotonic(), firm_id, company_id, bool(write), outcome,
                 max(0, connect_ms), max(0, lock_wait_ms), max(0, transaction_ms))
        with self._lock:
            self._events.append(event)

    def snapshot(self, *, firm_id, company_id=None):
        if not firm_id:
            raise ValueError("事务统计必须指定已授权事务所")
        cutoff = monotonic() - self.window_seconds
        with self._lock:
            rows = [e for e in self._events if e[0] >= cutoff and e[1] == firm_id
                    and (company_id is None or e[2] == company_id)]
        def stats(index):
            values = sorted(e[index] for e in rows)
            return {"mean_ms": round(sum(values) / len(values), 3) if values else None,
                    "p95_ms": round(values[max(0, (95 * len(values) + 99) // 100 - 1)], 3) if values else None,
                    "max_ms": round(values[-1], 3) if values else None}
        return {"scope": "current_process", "window_seconds": self.window_seconds,
                "sample_limit": self._events.maxlen, "count": len(rows),
                "writes": sum(e[3] for e in rows),
                "rollbacks": sum(e[4] == "rolled_back" for e in rows),
                "connection": stats(5), "lock_wait": stats(6), "transaction": stats(7)}
