"""Bounded process-local diagnostics. No SQL, credentials or business payloads."""

from collections import deque
from threading import Lock
from time import monotonic
from contextvars import ContextVar
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from threading import Event, Thread
from time import time
from uuid import uuid4

transaction_scope = ContextVar("accounting_transaction_scope", default=None)
transaction_actor = ContextVar("accounting_transaction_actor", default=None)


def tag_authorized_firm(actor):
    """Attribute a just-resolved workspace without changing database authority.

    Only identity discovery transactions may acquire their first firm tag.
    Callers must first validate the selected firm against current grants.
    Database.transaction resets this request-local value on every exit path.
    """
    current = transaction_actor.get()
    if current is None or current.user_id != actor.user_id or not actor.firm_id:
        raise ValueError("事务统计身份与已授权事务所不一致")
    if current.firm_id and current.firm_id != actor.firm_id:
        raise ValueError("事务统计不可切换至其他事务所")
    transaction_actor.set(actor)


def tag_authorized_company(company_id):
    scope = transaction_scope.get()
    if scope is not None:
        scope.add(company_id)


class TransactionMetrics:
    def __init__(self, capacity=4096, window_seconds=900):
        self._events = deque(maxlen=capacity)
        self._lock = Lock()
        self.window_seconds = window_seconds

    def record(
        self,
        *,
        firm_id,
        company_id=None,
        write=False,
        outcome="committed",
        connect_ms=0,
        lock_wait_ms=0,
        transaction_ms=0,
    ):
        if not firm_id:
            return  # System/worker-wide operations cannot be attributed to a tenant.
        event = (
            monotonic(),
            firm_id,
            company_id,
            bool(write),
            outcome,
            max(0, connect_ms),
            max(0, lock_wait_ms),
            max(0, transaction_ms),
        )
        with self._lock:
            self._events.append(event)

    def snapshot(self, *, firm_id, company_id=None):
        if not firm_id:
            raise ValueError("事务统计必须指定已授权事务所")
        cutoff = monotonic() - self.window_seconds
        with self._lock:
            rows = [
                e
                for e in self._events
                if e[0] >= cutoff and e[1] == firm_id and (company_id is None or e[2] == company_id)
            ]

        def stats(index):
            values = sorted(e[index] for e in rows)
            return {
                "mean_ms": round(sum(values) / len(values), 3) if values else None,
                "p95_ms": round(values[max(0, (95 * len(values) + 99) // 100 - 1)], 3)
                if values
                else None,
                "max_ms": round(values[-1], 3) if values else None,
            }

        return {
            "scope": "current_process",
            "window_seconds": self.window_seconds,
            "sample_limit": self._events.maxlen,
            "count": len(rows),
            "writes": sum(e[3] for e in rows),
            "rollbacks": sum(e[4] == "rolled_back" for e in rows),
            "connection_failures": sum(e[4] == "connection_failed" for e in rows),
            "connection": stats(5),
            "lock_wait": stats(6),
            "transaction": stats(7),
        }


@dataclass
class TransactionBucket:
    actor: object
    company_id: str
    stream_id: str
    minute: int
    revision: int = 0
    count: int = 0
    writes: int = 0
    rollbacks: int = 0
    connection_sum: float = 0
    lock_sum: float = 0
    transaction_sum: float = 0
    connection_max: float = 0
    lock_max: float = 0
    transaction_max: float = 0

    def payload(self):
        return (
            self.actor.firm_id,
            self.company_id,
            self.stream_id,
            self.minute,
            self.count,
            self.writes,
            self.rollbacks,
            self.connection_sum,
            self.lock_sum,
            self.transaction_sum,
            self.connection_max,
            self.lock_max,
            self.transaction_max,
        )


class SharedTransactionMetrics:
    """Minute aggregates, persisted separately from business/read transactions.

    Only explicitly attributed, authenticated company operations are stored.
    Each stream writes its cumulative minute snapshot, making repeated flushes
    idempotent. Memory, refresh batches and the company's retained window are
    bounded; no SQL, payload, hostname or credentials enter diagnostics.
    """

    def __init__(self, db, capacity=1024, window_seconds=900, flush_seconds=5):
        self.db, self.capacity = db, capacity
        self.window_seconds, self.flush_seconds = window_seconds, flush_seconds
        self._instance = uuid4().hex
        self._buckets = OrderedDict()
        self._pending = OrderedDict()
        self._lock, self._flush_lock = Lock(), Lock()
        self._stop = Event()
        self._thread = None
        self.dropped = 0

    def start(self):
        if self._thread is not None:
            return
        self._thread = Thread(target=self._loop, name="transaction-metrics", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(self.flush_seconds):
            self.flush()

    def record(
        self, *, actor, company_id, write, outcome, connect_ms, lock_wait_ms, transaction_ms
    ):
        if not actor or not actor.firm_id or not actor.user_id or not company_id:
            return
        minute = int(time() // 60)
        key = (minute, actor.firm_id, company_id, actor.user_id)
        durations = [
            min(86_400_000, max(0, value)) for value in (connect_ms, lock_wait_ms, transaction_ms)
        ]
        with self._lock:
            oldest = minute - (self.window_seconds + 59) // 60
            for expired in [item for item in self._buckets if item[0] < oldest]:
                self._buckets.pop(expired, None)
                self._pending.pop(expired, None)
            if key not in self._buckets:
                if len(self._buckets) >= self.capacity:
                    discarded, _ = self._buckets.popitem(last=False)
                    self._pending.pop(discarded, None)
                    self.dropped += 1
                stream = self._instance + sha256(actor.user_id.encode()).hexdigest()[:16]
                self._buckets[key] = TransactionBucket(actor, company_id, stream, minute)
            bucket = self._buckets[key]
            if bucket.count >= 1_000_000:
                self.dropped += 1
                return
            bucket.count += 1
            bucket.writes += bool(write)
            bucket.rollbacks += outcome == "rolled_back"
            bucket.revision += 1
            for value, total, maximum in zip(
                durations,
                ("connection_sum", "lock_sum", "transaction_sum"),
                ("connection_max", "lock_max", "transaction_max"),
            ):
                setattr(bucket, total, getattr(bucket, total) + value)
                setattr(bucket, maximum, max(getattr(bucket, maximum), value))
            self._pending.setdefault(key, None)

    def flush(self, batch_limit=16):
        if not self._flush_lock.acquire(blocking=False):
            return 0
        saved = 0
        try:
            with self._lock:
                work = [
                    (
                        key,
                        self._buckets[key].actor,
                        self._buckets[key].revision,
                        self._buckets[key].payload(),
                    )
                    for key in list(self._pending)[:batch_limit]
                ]
            for key, actor, revision, payload in work:
                if key[0] < int(time() // 60) - 15:
                    with self._lock:
                        self._pending.pop(key, None)
                    continue
                try:
                    self.db.persist_transaction_bucket(actor, payload)
                except Exception:
                    # Telemetry must never affect a committed business result.
                    # Rotate a failed/revoked scope behind other companies.
                    with self._lock:
                        if key in self._pending:
                            self._pending.move_to_end(key)
                    continue
                saved += 1
                with self._lock:
                    current = self._buckets.get(key)
                    if current and current.revision == revision:
                        self._pending.pop(key, None)
                    elif key in self._pending:
                        self._pending.move_to_end(key)
            return saved
        finally:
            self._flush_lock.release()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.flush()
