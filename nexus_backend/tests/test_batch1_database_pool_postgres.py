"""Real bounded pool reuse, FORCE RLS and rollback with isolated test DSNs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from time import time
import os

import pytest

from app.accounting.database import Database
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService
from test_postgres_integration import ADMIN_URL, APP_URL, case, peer, postgres
from test_intelligence_workflow_postgres import pg_ai
from test_business_postgres import business_case, business_postgres
from test_queue_worker_postgres import pg_queue, enqueue

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要独立本机 PostgreSQL 管理员和受限测试 DSN")


def test_single_connection_reused_without_role_identity_or_failed_transaction(case, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "1")
    db = Database(APP_URL)
    try:
        with db.transaction(actor=case.actor) as conn:
            pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            assert conn.execute("SELECT current_user,accounting.actor_user(),accounting.actor_firm()").fetchone()[:] == ("accounting_app", case.actor.user_id, case.actor.firm_id)
            assert conn.raw.prepare_threshold is None
        with pytest.raises(Exception):
            with db.transaction(write=True, actor=case.actor) as conn:
                conn.execute("SELECT 1/0")
        with closing(db.connect()) as conn:
            assert conn.execute("SELECT pg_backend_pid()").fetchone()[0] == pid
            user, firm, role, login = conn.execute("SELECT current_setting('app.user_id',true),current_setting('app.firm_id',true),current_user,session_user").fetchone()[:]
            assert not user and not firm and role == login
        outsider = peer(case, firm_role="owner", other_firm=True)
        with db.transaction(actor=outsider) as conn:
            assert conn.execute("SELECT * FROM books WHERE id=?", (case.book,)).fetchall() == []
        with db.transaction(actor=case.actor) as conn:
            assert conn.execute("SELECT id FROM books WHERE id=?", (case.book,)).fetchone()[0] == case.book
        assert db.pool_snapshot()["pool_size"] == 1
    finally:
        db.close()


def test_concurrent_actor_transactions_stay_scoped_under_pool_bound(case, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "2")
    db = Database(APP_URL)
    outsider = peer(case, firm_role="owner", other_firm=True)
    other_book = AccountingService(db).create_book(outsider, BookCreate(name="其他客户", period="2026-10"))
    try:
        def read(index):
            actor, own, hidden = (case.actor, case.book, other_book["id"]) if index % 2 else (outsider, other_book["id"], case.book)
            with db.transaction(actor=actor) as conn:
                assert conn.execute("SELECT accounting.actor_user()").fetchone()[0] == actor.user_id
                assert conn.execute("SELECT id FROM books WHERE id=?", (own,)).fetchone()[0] == own
                assert conn.execute("SELECT id FROM books WHERE id=?", (hidden,)).fetchall() == []
                conn.execute("SELECT pg_sleep(0.01)")
        with ThreadPoolExecutor(max_workers=8) as threads:
            list(threads.map(read, range(24)))
        assert db.pool_snapshot()["pool_size"] <= 2
    finally:
        db.close()


def test_pool_wait_timeout_returns_cleanly_and_releases_capacity(case, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "1")
    monkeypatch.setenv("ACCOUNTING_DB_POOL_TIMEOUT_SECONDS", "0.1")
    db = Database(APP_URL)
    try:
        held = db.connect()
        with pytest.raises(RuntimeError, match="连接池等待超时") as caught:
            db.connect()
        assert APP_URL not in str(caught.value)
        held.close()
        with db.transaction(actor=case.actor) as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        db.close()


def test_cross_process_buckets_manager_rls_snapshot_retry_and_revoke(case):
    db = Database(APP_URL)
    try:
        for instance in (case.db, db):
            with instance.transaction(actor=case.actor, company_id=case.company):
                pass
            assert instance.shared_metrics.flush() >= 1
        with case.db.transaction(actor=case.actor) as conn:
            before = conn.execute("SELECT count(*),sum(tx_count) FROM transaction_metric_buckets WHERE company_id=?", (case.company,)).fetchone()[:]
            assert before[0] >= 2
            payload = (case.actor.firm_id, case.company, "idempotent", int(time() // 60), 1, 0, 0, 2, 3, 4, 2, 3, 4)
        db.persist_transaction_bucket(case.actor, payload)
        db.persist_transaction_bucket(case.actor, payload)
        outsider = peer(case, firm_role="owner", other_firm=True)
        with case.db.transaction(actor=outsider) as conn:
            assert not conn.execute("SELECT * FROM transaction_metric_buckets WHERE company_id=?", (case.company,)).fetchall()
            since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            with pytest.raises(Exception):
                conn.execute("SELECT accounting.company_runtime_samples(?,?,?)", (case.company, since, 500))
        with pytest.raises(Exception):
            db.persist_transaction_bucket(outsider, payload)
        with case.db.transaction(actor=case.actor) as conn:
            after = conn.execute("SELECT count(*) FROM transaction_metric_buckets WHERE company_id=? AND stream_id='idempotent'", (case.company,)).fetchone()[0]
            assert after == 1
            # Table writes cannot bypass the validated function.
            with pytest.raises(Exception):
                conn.execute("DELETE FROM transaction_metric_buckets WHERE company_id=?", (case.company,))
    finally:
        db.close()


def test_company_runtime_samples_return_only_bounded_timing_fields(case):
    since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    with case.db.transaction(actor=case.actor) as conn:
        assert conn.execute("SELECT accounting.company_runtime_samples(?,?,?)", (case.company, since, 500)).fetchone()[0] == []
    for limit in (0, 501):
        with pytest.raises(Exception):
            with case.db.transaction(actor=case.actor) as conn:
                conn.execute("SELECT accounting.company_runtime_samples(?,?,?)", (case.company, since, limit))
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with pytest.raises(Exception):
        with case.db.transaction(actor=case.actor) as conn:
            conn.execute("SELECT accounting.company_runtime_samples(?,?,?)", (case.company, old, 500))


@pytest.mark.skipif(not os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL"), reason="需要独立 worker 测试 DSN")
def test_actual_worker_timings_and_transactions_aggregate_without_private_evidence(pg_queue):
    from app.operations.meter import MeterService
    from app.operations.service import OperationsService
    from app.operations.telemetry import TelemetryService
    from test_intelligence_workflow import create_event
    c = pg_queue
    event = create_event(c)
    enqueue(c, event)
    assert c.worker.run_once()
    c.db.shared_metrics.flush()
    c.worker.queue.db.shared_metrics.flush()
    report = TelemetryService(c.db, OperationsService(c.db, MeterService(c.db))).overview(c.manager, c.company["id"])
    ocr = next(value for value in report["queue_runtime"]["kinds"] if value["kind"] == "ocr")
    assert ocr["attempts"] == ocr["succeeded_attempts"] == 1
    assert ocr["queue_wait"]["samples"] == ocr["processing"]["samples"] == 1
    assert report["shared_transactions"]["streams"] >= 2
    assert report["shared_transactions"]["count"] >= 2
    assert "payload" not in str(report) and "question" not in str(report)


def test_pg_api_startup_failure_closes_pool_and_diagnostic_thread(case, monkeypatch):
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app import main
    captured = []
    def database(*args, **kwargs):
        db = Database(*args, **kwargs)
        captured.append(db)
        return db
    class BrokenStore:
        def verify_private(self):
            raise RuntimeError("private bucket unavailable")
    monkeypatch.setenv("ACCOUNTING_ENV", "test")
    for key in ("APP_ENV", "ENV", "ENVIRONMENT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(main, "Database", database)
    monkeypatch.setattr(main, "create_attachment_store", lambda *_, **__: BrokenStore())
    app = main.create_app(db_path=APP_URL, auth_settings=SimpleNamespace(mode="supabase"), seed=False, queue_worker_enabled=False)
    with pytest.raises(RuntimeError, match="private bucket unavailable"):
        with TestClient(app):
            pass
    assert captured[0]._closed and captured[0]._pool.closed
    assert not captured[0].shared_metrics._thread.is_alive()
