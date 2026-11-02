"""Actual restricted PG: bounded waiting, no resubmission, no identity leakage."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, Event
from time import monotonic, sleep

import pytest

from app.accounting.database import Database, DatabaseCapacityError
from test_postgres_integration import ADMIN_URL, APP_URL, case, peer, postgres  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="Isolated PG required")


def wait_for(predicate, seconds=3):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Expected pool state not reached")


def test_fifty_simultaneous_borrowers_wait_without_growing_pool(case, monkeypatch):
    for key in (
        "ACCOUNTING_DB_POOL_MAX",
        "ACCOUNTING_DB_POOL_MAX_WAITING",
        "ACCOUNTING_DB_POOL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    with closing(Database(APP_URL)) as db:
        pool = db._postgres_pool()
        # Occupy all four connections before starting the burst. No external
        # request/model is retried: each of the 50 borrowers calls once.
        held = [db.connect() for _ in range(4)]
        barrier = Barrier(51)
        outsider = peer(case, firm_role="owner", other_firm=True)

        def borrow(index):
            barrier.wait(timeout=5)
            actor = case.actor if index % 2 else outsider
            with db.transaction(actor=actor) as conn:
                assert conn.execute("SELECT accounting.actor_user()").fetchone()[0] == actor.user_id
                visible = conn.execute("SELECT id FROM books WHERE id=?", (case.book,)).fetchall()
                assert bool(visible) == bool(index % 2)
                conn.execute("SELECT pg_sleep(0.005)")
            return index

        with ThreadPoolExecutor(50) as threads:
            futures = [threads.submit(borrow, i) for i in range(50)]
            barrier.wait(timeout=5)
            try:
                wait_for(
                    lambda: (
                        pool.get_stats()["requests_waiting"] + sum(f.done() for f in futures) >= 50
                    )
                )
                assert not any(f.done() for f in futures), (
                    "Burst rejected instead of applying backpressure"
                )
            finally:
                for connection in held:
                    connection.close()
            assert sorted(f.result(timeout=10) for f in futures) == list(range(50))
        assert pool.max_size == 4
        assert pool.get_stats()["pool_size"] <= 4
        assert pool.get_stats()["requests_waiting"] == 0


def test_overflow_is_bounded_timeout_expires_and_later_request_succeeds(case, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "1")
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX_WAITING", "2")
    monkeypatch.setenv("ACCOUNTING_DB_POOL_TIMEOUT_SECONDS", "0.2")
    with closing(Database(APP_URL)) as db:
        held = db.connect()
        pool = db._postgres_pool()

        def borrow():
            started = monotonic()
            with pytest.raises(DatabaseCapacityError) as error:
                db.connect()
            return error.value, monotonic() - started

        with ThreadPoolExecutor(2) as threads:
            futures = [threads.submit(borrow) for _ in range(2)]
            try:
                wait_for(lambda: pool.get_stats()["requests_waiting"] == 2)
                started = monotonic()
                with pytest.raises(DatabaseCapacityError) as full:
                    db.connect()
                assert monotonic() - started < 0.15
                assert full.value.reason == "queue_full"
                for future in futures:
                    error, elapsed = future.result(timeout=2)
                    assert error.reason == "wait_timeout"
                    assert 0.15 <= elapsed < 1
                    assert APP_URL not in str(error)
            finally:
                held.close()
        with db.transaction(actor=case.actor) as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        assert pool.get_stats()["requests_waiting"] == 0


def test_queued_requests_do_not_outlive_database_close(case, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "1")
    db = Database(APP_URL)
    held = db.connect()
    try:
        with ThreadPoolExecutor(1) as threads:
            future = threads.submit(db.connect)
            wait_for(lambda: db._pool.get_stats()["requests_waiting"] == 1)
            db.close()
            with pytest.raises(RuntimeError):
                future.result(timeout=2)
    finally:
        held.close()
        db.close()


def test_fifo_waiters_are_not_overtaken_by_new_arrivals(case, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "1")
    with closing(Database(APP_URL)) as db:
        held = db.connect()
        order = []

        def borrow(index):
            with closing(db.connect()):
                order.append(index)

        with ThreadPoolExecutor(6) as threads:
            futures = []
            try:
                for i in range(5):
                    futures.append(threads.submit(borrow, i))
                    wait_for(lambda: db._pool.get_stats()["requests_waiting"] == i + 1)
            finally:
                held.close()
            futures.append(threads.submit(borrow, 5))
            for future in futures:
                future.result(timeout=5)
        assert order == list(range(6))


def test_permission_revoked_while_waiting_is_checked_after_admission(case, monkeypatch):
    from app.accounting.service import AccountingService
    from app.accounting.domain import AccountingError

    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "1")
    with closing(Database(APP_URL)) as db:
        service = AccountingService(db)
        assert service.get_book(case.reviewer, case.book)["book"]["id"] == case.book
        held = db.connect()
        with ThreadPoolExecutor(1) as threads:
            future = threads.submit(service.get_book, case.reviewer, case.book)
            try:
                wait_for(lambda: db._pool.get_stats()["requests_waiting"] == 1)
                with case.psycopg.connect(ADMIN_URL) as conn:
                    conn.execute(
                        "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                        (case.book, case.reviewer.user_id),
                    )
            finally:
                held.close()
            with pytest.raises(AccountingError) as denied:
                future.result(timeout=5)
            assert denied.value.status == 404
        with db.transaction(actor=case.reviewer) as conn:
            assert conn.execute("SELECT id FROM books WHERE id=?", (case.book,)).fetchall() == []
