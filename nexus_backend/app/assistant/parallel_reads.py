"""Two admitted read branches, no tools supplied by clients, no model or writes.

Capacity is process-wide. A timed-out running reader retains its slot until it
really finishes; its late result is never accepted or used to perform an action.
"""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import BoundedSemaphore
import math
import os
import time

from app.accounting.domain import AccountingError
from app.accounting.time_budget import database_deadline
from . import investigation as domain

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="evidence-read")
_SLOTS = BoundedSemaphore(2)


def _failure(error):
    if (
        isinstance(error, TimeoutError)
        or getattr(error, "reason", None) == "wait_timeout"
        or getattr(error, "sqlstate", None) in {"57014", "55P03"}
    ):
        return "timeout"
    return "refused" if isinstance(error, AccountingError) else "failed"


def _reader(service, actor, book_id, period, name, deadline, on_step):
    with database_deadline(deadline):
        return _read(service, actor, book_id, period, name, deadline, on_step)


def _read(service, actor, book_id, period, name, deadline, on_step):
    if time.monotonic() >= deadline:
        raise TimeoutError()
    if on_step:
        on_step(name)
    with service.db.transaction(actor=actor) as conn:
        book = service.access.book(conn, actor, book_id, "read")
        if book["period"] != period:
            raise AccountingError("调查月份与账套不一致", 422)
        facts = domain.READERS[name](conn, book_id, period)
    with service.db.transaction(actor=actor) as conn:
        current = service.access.book(conn, actor, book_id, "read")
        if current["company_id"] != book["company_id"] or current["period"] != period:
            raise AccountingError("调查范围已变化", 409)
    if time.monotonic() >= deadline:
        raise TimeoutError()
    return facts


def _submit(*args):
    if not _SLOTS.acquire(blocking=False):
        return None
    try:
        future = _POOL.submit(_reader, *args)
    except BaseException:
        _SLOTS.release()
        raise
    future.add_done_callback(lambda _: _SLOTS.release())
    return future


def run(service, actor, book_id, period, *, parallelism, on_step=None):
    start = time.monotonic()
    reason, facts, attempted, states, pending = None, {}, [], {}, {}
    try:
        seconds = float(os.getenv("ACCOUNTING_ASSISTANT_READ_SECONDS", "3"))
        if parallelism != "2" or not math.isfinite(seconds) or not 0 < seconds <= 10:
            raise ValueError()
    except (TypeError, ValueError):
        reason, seconds = "invalid_budget", 0
    deadline = start + seconds
    # Reject a foreign scope before allocating any branch or opening evidence.
    if reason is None:
        try:
            with database_deadline(deadline), service.db.transaction(actor=actor) as conn:
                book = service.access.book(conn, actor, book_id, "read")
                if book["period"] != period:
                    raise AccountingError("调查月份与账套不一致", 422)
        except Exception as error:
            reason = _failure(error)
    cursor = 0
    while reason is None and (cursor < len(domain.TOOLS) or pending):
        if time.monotonic() >= deadline:
            reason = "timeout"
            break
        while cursor < len(domain.TOOLS) and len(pending) < 2:
            name = domain.TOOLS[cursor]
            future = _submit(service, actor, book_id, period, name, deadline, on_step)
            if future is None:
                if not pending:
                    reason = "busy"
                break
            pending[future] = name
            attempted.append(name)
            cursor += 1
        if reason:
            break
        done, _ = wait(
            pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED
        )
        if not done:
            reason = "timeout"
            break
        for future in done:
            name = pending.pop(future)
            try:
                facts[name] = future.result()
                states[name] = {"tool": name, "ok": True}
            except Exception as error:
                reason = _failure(error)
                states[name] = {"tool": name, "ok": False, "code": reason}
        # No new branch after the first failure, even if another completed.
    for future, name in pending.items():
        future.cancel()
        states[name] = {"tool": name, "ok": False, "code": reason or "cancelled"}
    if reason:
        facts = {}
    ordered = {name: facts[name] for name in domain.TOOLS if name in facts}
    steps = [
        states.get(name, {"tool": name, "ok": False, "code": reason or "failed"})
        for name in attempted
    ]
    metrics = {"validation_reads": 0}
    with database_deadline(deadline):
        result = domain.finish(
            service,
            actor,
            book_id,
            period,
            ordered,
            [n for n in domain.TOOLS if n not in ordered],
            steps,
            "blocked" if reason else "complete",
            domain._now(),
            deadline=deadline,
            metrics=metrics,
        )
    if result["status"] == "blocked" and reason is None:
        reason = "timeout" if time.monotonic() >= deadline else "evidence_changed"
    result["execution"] = {
        "mode": "bounded_parallel",
        "parallelism": 2,
        "max_reads": 3,
        "attempted_reads": len(attempted),
        "accepted_reads": len(result["findings"]),
        "validation_reads": metrics["validation_reads"],
        "max_total_reads": 6,
        "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
        "budget_seconds": seconds,
        "stop_reason": reason,
        "model_calls": 0,
        "tokens": 0,
        "cost_cny": None,
    }
    return result
