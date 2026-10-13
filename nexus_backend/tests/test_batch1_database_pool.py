"""Bounded diagnostics and lifecycle regression without external services."""
from time import time

import pytest

from app.accounting.database import Database, PostgresConnection
from app.accounting.domain import Actor
from app.accounting.models import BookCreate
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService
from app.accounting.telemetry import SharedTransactionMetrics
from app.operations.telemetry import TelemetryService
from test_stage7_operations import ops_case
from test_model_cost_workflow import cost_case
from test_intelligence_workflow import ai_case
from test_stage7_proposal_queue import proposal_cost_case


def test_pool_return_rolls_back_once_and_discards_broken_connection():
    class Raw:
        rollbacks = 0
        closed = False
        def rollback(self):
            self.rollbacks += 1
            raise RuntimeError("broken socket")
        def close(self):
            self.closed = True
    class Pool:
        returned = []
        def putconn(self, value):
            self.returned.append(value)
    raw, pool = Raw(), Pool()
    conn = PostgresConnection(raw, pool)
    conn.close()
    conn.close()
    assert raw.rollbacks == 1 and raw.closed and pool.returned == [raw]
    with pytest.raises(RuntimeError, match="已归还"):
        conn.execute("SELECT 1")


def test_pool_settings_reject_unbounded_values(monkeypatch):
    for value in ("0", "33", "nan", "invalid"):
        monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", value)
        with pytest.raises(ValueError, match="ACCOUNTING_DB_POOL_MAX"):
            Database._bounded_setting("ACCOUNTING_DB_POOL_MAX", 4, 1, 32)


def test_telemetry_buffer_bounded_batch_and_idempotent_snapshot():
    class Sink:
        def __init__(self):
            self.saved = {}
        def persist_transaction_bucket(self, actor, payload):
            self.saved[payload[:4]] = payload
    sink = Sink()
    metrics = SharedTransactionMetrics(sink, capacity=2)
    actor = Actor("user", "firm")
    def record(company):
        metrics.record(actor=actor, company_id=company, write=True, outcome="rolled_back",
                       connect_ms=1, lock_wait_ms=2, transaction_ms=3)
    record("a")
    record("b")
    record("c")
    assert metrics.dropped == 1
    assert metrics.flush(batch_limit=1) == 1 and len(sink.saved) == 1
    assert metrics.flush(batch_limit=1) == 1 and len(sink.saved) == 2
    record("c")
    assert metrics.flush() == 1 and len(sink.saved) == 2
    assert sum(payload[4] for payload in sink.saved.values()) == 3
    metrics.record(actor=None, company_id="c", write=True, outcome="committed",
                   connect_ms=999, lock_wait_ms=999, transaction_ms=999)
    assert metrics.flush() == 0


def test_two_database_instances_share_scoped_observations(tmp_path):
    db1 = Database(tmp_path / "shared.db")
    service = AccountingService(db1)
    bootstrap(service, seed_examples=False)
    book = service.create_book(LOCAL_ACTOR, BookCreate(name="统计企业", period="2026-10"))
    db2 = Database(db1.path)
    for db in (db1, db2):
        with db.transaction(actor=LOCAL_ACTOR, company_id=book["company_id"]) as conn:
            conn.execute("SELECT 1")
        assert db.shared_metrics.flush() >= 1
    with db1.transaction() as conn:
        rows = conn.execute("SELECT DISTINCT stream_id FROM transaction_metric_buckets WHERE company_id=?", (book["company_id"],)).fetchall()
        assert len(rows) == 2
    # Repeated snapshot flush never adds a duplicate minute row.
    with db2.transaction(actor=LOCAL_ACTOR, company_id=book["company_id"]):
        pass
    db2.shared_metrics.flush()
    with db1.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transaction_metric_buckets").fetchone()[0] == 2
    db2.close()
    db2.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        db2.connect()
    db1.close()


def test_telemetry_manager_acl_is_rechecked_and_reads_do_not_flush(ops_case, monkeypatch):
    c = ops_case
    with c.db.transaction(actor=c.manager, company_id=c.company["id"]):
        pass
    c.db.shared_metrics.flush()
    monkeypatch.setattr(c.db, "persist_transaction_bucket", lambda *_: pytest.fail("GET must not write telemetry"))
    value = TelemetryService(c.db, c.operations).overview(c.manager, c.company["id"])
    assert value["shared_transactions"]["scope"] == "all_processes"
    assert value["shared_transactions"]["count"] >= 1
    assert value["queue_runtime"]["sample_limit_per_kind"] == 500
    assert value["pool"]["enabled"] is False
    for actor in (c.actors["employee"], c.actors["finance"]):
        with pytest.raises(Exception):
            TelemetryService(c.db, c.operations).overview(actor, c.company["id"])


def test_expired_metric_cleanup_is_bounded(tmp_path):
    db = Database(tmp_path / "expires.db")
    service = AccountingService(db)
    bootstrap(service, seed_examples=False)
    book = service.create_book(LOCAL_ACTOR, BookCreate(name="过期指标", period="2026-10"))
    payload = (LOCAL_ACTOR.firm_id, book["company_id"], "expired", int(time() // 60) - 20,
               1, 0, 0, 1, 1, 1, 1, 1, 1)
    # Fixture-only direct write simulates diagnostics left by a stopped process.
    with db.transaction(write=True) as conn:
        conn.execute("INSERT INTO transaction_metric_buckets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    db.shared_metrics.record(actor=LOCAL_ACTOR, company_id=book["company_id"], write=False,
                             outcome="committed", connect_ms=1, lock_wait_ms=0, transaction_ms=1)
    db.shared_metrics.flush()
    with db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM transaction_metric_buckets WHERE stream_id='expired'").fetchone()
    db.close()


def test_api_startup_failure_closes_database_before_lifespan_yield(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    captured = []
    def database(*args, **kwargs):
        db = Database(*args, **kwargs)
        captured.append(db)
        return db
    class BrokenStore:
        def verify_private(self):
            raise RuntimeError("private bucket unavailable")
    monkeypatch.setattr(main, "Database", database)
    monkeypatch.setattr(main, "create_attachment_store", lambda *_, **__: BrokenStore())
    app = main.create_app(db_path=tmp_path / "startup.db", seed=False, queue_worker_enabled=False)
    with pytest.raises(RuntimeError, match="private bucket unavailable"):
        with TestClient(app):
            pass
    assert len(captured) == 1 and captured[0]._closed
    assert captured[0].shared_metrics._thread is None


def test_revoked_company_scope_does_not_publish_pending_metrics(tmp_path):
    from app.accounting.domain import Actor
    from uuid import uuid4
    db = Database(tmp_path / "revoke.db")
    service = AccountingService(db)
    bootstrap(service, seed_examples=False)
    book = service.create_book(LOCAL_ACTOR, BookCreate(name="撤权统计", period="2026-10"))
    db.shared_metrics.flush()
    with db.transaction() as conn:
        before = conn.execute("SELECT COALESCE(SUM(tx_count),0) FROM transaction_metric_buckets").fetchone()[0]
    peer = Actor(uuid4().hex, LOCAL_ACTOR.firm_id)
    with db.transaction(write=True) as conn:
        conn.execute("INSERT INTO users(id,name) VALUES(?,'统计员工')", (peer.user_id,))
        conn.execute("INSERT INTO company_memberships(company_id,user_id,role) VALUES(?,?,'employee')", (book["company_id"], peer.user_id))
    db.shared_metrics.record(actor=peer, company_id=book["company_id"], write=False,
                             outcome="committed", connect_ms=1, lock_wait_ms=0, transaction_ms=1)
    with db.transaction(write=True) as conn:
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (book["company_id"], peer.user_id))
    db.shared_metrics.flush()
    with db.transaction() as conn:
        assert conn.execute("SELECT COALESCE(SUM(tx_count),0) FROM transaction_metric_buckets").fetchone()[0] == before
    db.close()


def test_queue_runtime_uses_persisted_attempts_and_reports_first_wait(ops_case):
    from datetime import datetime, timezone
    from app.jobs.requests import BatchRequest
    from test_intelligence_workflow import create_event
    c = ops_case
    c.queue.clock = lambda: datetime.now(timezone.utc)
    event = create_event(c)
    c.queue.enqueue(c.actors["employee"], BatchRequest(
        company_id=c.company["id"], idempotency_key="timing-fixture",
        items=[{"event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"]}],
    ))
    assert c.worker.run_once()
    report = TelemetryService(c.db, c.operations).overview(c.manager, c.company["id"])
    ocr = next(row for row in report["queue_runtime"]["kinds"] if row["kind"] == "ocr")
    assert ocr["attempts"] == ocr["completed_attempts"] == ocr["succeeded_attempts"] == 1
    assert ocr["queue_wait"]["samples"] == ocr["processing"]["samples"] == 1
    assert ocr["processing"]["mean_ms"] >= 0 and not ocr["sample_limit_reached"]
