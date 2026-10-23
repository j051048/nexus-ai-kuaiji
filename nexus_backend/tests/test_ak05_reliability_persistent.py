"""Synthetic shared SQLite evidence, with actual queues and fake providers."""

import threading
import time
from uuid import uuid4

from app.assistant.models import ConversationCreate, TurnCreate
from app.unified_worker import UnifiedWorker
from test_intelligence_workflow import ai_case
from test_model_cost_workflow import cost_case
from test_queue_workflow import create_event, enqueue, queue_case
from test_stage15_assistant_meter import chat_case


def test_late_other_company_assistant_finishes_before_blocked_ocr(queue_case, chat_case):
    case = queue_case
    assert case.db is chat_case.db
    event = create_event(case)
    job_id = enqueue(case, [event])["jobs"][0]["id"]
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def slow_ocr():
        entered.set()
        try:
            assert release.wait(15), "OCR guard expired before assertion"
        finally:
            exited.set()

    case.provider.extract_hook = slow_ocr
    worker = UnifiedWorker(
        assistant_worker=case.chat_worker, queue_worker=case.worker, idle_seconds=0.05
    )
    worker.monitor_interval = 0.02
    worker.start()
    try:
        assert entered.wait(5)
        actor = case.manager
        conversation = case.chat_service.create(
            actor,
            ConversationCreate(
                company_id=case.other_company["id"],
                title="late synthetic company B",
                idempotency_key=uuid4().hex,
            ),
        )["conversation"]
        turn = case.chat_service.enqueue(
            actor,
            conversation["id"],
            TurnCreate(
                question="当前企业资料是否完整？",
                expected_scope_version=1,
                idempotency_key=uuid4().hex,
            ),
        )["turn"]
        deadline = time.monotonic() + 6
        status = None
        while time.monotonic() < deadline:
            status = case.chat_service.turn(actor, conversation["id"], turn["id"])["turn"]["status"]
            if status == "succeeded":
                break
            time.sleep(0.01)
        assert status == "succeeded"
        assert not exited.is_set()
        with case.db.transaction() as conn:
            assert (
                conn.execute("SELECT status FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()[0]
                == "running"
            )
        assert case.chat_provider.calls == 1
    finally:
        release.set()
        worker.stop()
        assert worker.wait_for_idle(5)


def test_blocked_claim_is_not_reported_as_claimed_work(chat_case, monkeypatch):
    case = chat_case
    entered, release = threading.Event(), threading.Event()
    claim = case.chat_worker.claim

    def slow_claim():
        entered.set()
        assert release.wait(10)
        return claim()

    monkeypatch.setattr(case.chat_worker, "claim", slow_claim)
    worker = UnifiedWorker(assistant_worker=case.chat_worker, idle_seconds=0.05)
    worker.start()
    try:
        assert entered.wait(2)
        worker._monitor_tick("active")
        with case.db.transaction() as conn:
            row = conn.execute(
                "SELECT state,inflight_count FROM assistant_worker_heartbeats WHERE worker_id=?",
                (case.chat_worker.worker_id,),
            ).fetchone()
            assert row["inflight_count"] == 0 and row["state"] == "idle"
    finally:
        release.set()
        worker.stop()
        assert worker.wait_for_idle(5)


def test_ocr_heartbeat_timestamp_advances_while_draining(queue_case):
    case = queue_case
    enqueue(case, [create_event(case)])
    entered, release = threading.Event(), threading.Event()

    def slow():
        entered.set()
        assert release.wait(10)

    case.provider.extract_hook = slow
    case.worker.heartbeat_seconds = 0.02
    worker = UnifiedWorker(queue_worker=case.worker, idle_seconds=0.05)
    worker.monitor_interval = 0.02
    worker.start()
    try:
        assert entered.wait(3)
        worker.stop()
        stamps = set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(stamps) < 2:
            with case.db.transaction() as conn:
                row = conn.execute(
                    "SELECT heartbeat_at,state,inflight_count FROM worker_heartbeats WHERE worker_id=?",
                    (case.worker.worker_id,),
                ).fetchone()
            if row and row["inflight_count"] == 1 and row["state"] == "active":
                stamps.add(row["heartbeat_at"])
            time.sleep(0.02)
        assert len(stamps) >= 2
        assert not worker.wait_for_idle(0.02)
    finally:
        release.set()
        worker.stop()
        assert worker.wait_for_idle(5)
    with case.db.transaction() as conn:
        row = conn.execute(
            "SELECT state,inflight_count FROM worker_heartbeats WHERE worker_id=?",
            (case.worker.worker_id,),
        ).fetchone()
        assert row["state"] == "stopped" and row["inflight_count"] == 0


def test_broken_maintenance_does_not_strand_a_real_job(queue_case, monkeypatch):
    case = queue_case
    enqueue(case, [create_event(case)])

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic refresh failure")

    monkeypatch.setattr(case.worker, "maintenance", broken)
    assert case.worker.run_once()
    assert case.provider.extract_calls == 1
    with case.db.transaction() as conn:
        assert conn.execute("SELECT status FROM ocr_jobs").fetchone()[0] == "succeeded"
