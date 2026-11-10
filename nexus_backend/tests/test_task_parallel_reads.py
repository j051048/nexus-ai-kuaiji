from threading import Barrier, Lock
from threading import Event
import time
import pytest

from app.assistant import investigation
from test_business import business  # noqa: F401


def test_two_readers_overlap_without_sharing_connection(business, monkeypatch):
    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "2")
    barrier, lock = Barrier(2, timeout=1), Lock()
    connections, calls = [], {}
    for name in investigation.TOOLS[:2]:
        original = investigation.READERS[name]

        def reader(conn, book_id, period, name=name, original=original):
            with lock:
                calls[name] = calls.get(name, 0) + 1
                first = calls[name] == 1
                if first:
                    connections.append(conn)
            if first:
                barrier.wait()
            return original(conn, book_id, period)

        monkeypatch.setitem(investigation.READERS, name, reader)
    result = investigation.investigate(
        business.service, business.manager, business.book["id"], "2026-10"
    )
    assert result["status"] == "complete"
    assert connections[0] is not connections[1]
    assert result["execution"]["parallelism"] == 2
    assert result["execution"]["model_calls"] == 0
    assert result["execution"]["cost_cny"] is None


def test_parallel_invalid_budget_fails_closed(business, monkeypatch):
    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "200")
    result = investigation.investigate(
        business.service, business.manager, business.book["id"], "2026-10"
    )
    assert result["status"] == "blocked"
    assert result["findings"] == {}
    assert result["execution"]["stop_reason"] == "invalid_budget"


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf", "11", "true"])
def test_parallel_bad_time_budget_never_reads(business, monkeypatch, budget):
    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "2")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_SECONDS", budget)
    result = investigation.investigate(
        business.service, business.manager, business.book["id"], "2026-10"
    )
    assert result["findings"] == {}
    assert result["execution"]["attempted_reads"] == 0


def test_parallel_late_results_hold_slots_and_never_publish(business, monkeypatch):
    from app.assistant import parallel_reads

    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "2")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_SECONDS", "0.15")
    release = Event()
    entered = Barrier(2, timeout=1)

    def slow(*args):
        entered.wait()
        release.wait(5)
        return {"late": True}

    for name in investigation.TOOLS[:2]:
        monkeypatch.setitem(investigation.READERS, name, slow)
    started = time.monotonic()
    try:
        first = investigation.investigate(
            business.service, business.manager, business.book["id"], "2026-10"
        )
        assert time.monotonic() - started < 2
        assert first["execution"]["stop_reason"] == "timeout"
        assert first["findings"] == {} and first["evidence"] is None
        second = investigation.investigate(
            business.service, business.manager, business.book["id"], "2026-10"
        )
        assert second["execution"]["stop_reason"] == "busy"
        assert second["execution"]["attempted_reads"] == 0
    finally:
        release.set()
        # Synchronize completion before restoring injected readers for other tests.
        parallel_reads._POOL.submit(lambda: None).result(timeout=5)
        parallel_reads._POOL.submit(lambda: None).result(timeout=5)
    assert first["findings"] == {}


def test_parallel_final_change_discards_every_branch(business, monkeypatch):
    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "2")
    original = investigation.READERS["voucher_status"]
    calls = 0

    def changed(*args):
        nonlocal calls
        calls += 1
        return {**original(*args), "draft_rows": calls}

    monkeypatch.setitem(investigation.READERS, "voucher_status", changed)
    result = investigation.investigate(
        business.service, business.manager, business.book["id"], "2026-10"
    )
    assert result["status"] == "blocked" and result["findings"] == {}
    assert result["execution"]["validation_reads"] == 1
