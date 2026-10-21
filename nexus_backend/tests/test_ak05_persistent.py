"""AK05: real SQLite queues, one slow OCR and a later assistant turn."""
import threading
import time

from uuid import uuid4

from app.assistant.models import ConversationCreate, TurnCreate
from app.unified_worker import UnifiedWorker
from test_intelligence_workflow import ai_case
from test_model_cost_workflow import cost_case
from test_queue_workflow import create_event, enqueue, queue_case
from test_stage15_assistant_meter import chat_case, question


def test_revoke_during_assistant_provider_call_never_publishes(chat_case):
    case = chat_case
    actor, conversation, turn = question(case, case.actors["employee"])
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        release.wait(5)

    case.chat_provider.hook = block
    worker = threading.Thread(target=case.chat_worker.run_once)
    worker.start()
    try:
        assert started.wait(2)
        case.directory.revoke_company_member(
            case.manager, case.company["id"], case.actors["employee"].user_id
        )
        release.set()
        worker.join(5)
        assert not worker.is_alive()
    finally:
        release.set()
    with case.db.transaction() as conn:
        assert conn.execute(
            "SELECT status FROM assistant_turns WHERE id=?", (turn["id"],)
        ).fetchone()[0] == "stale"
        assert conn.execute(
            "SELECT COUNT(*) FROM assistant_messages WHERE turn_id=? AND role='assistant'", (turn["id"],)
        ).fetchone()[0] == 0


def test_restart_does_not_resend_a_leased_job(queue_case):
    case = queue_case
    enqueue(case, [create_event(case)])
    job = case.queue.claim("dead-process", lease_seconds=180, concurrency=2)
    assert job is not None
    # The dead process never released its lease. A fresh worker must not
    # claim or send the same job immediately.
    assert case.worker.run_once() in {None, False}
    assert case.provider.extract_calls == 0


def test_slow_ocr_keeps_persistent_inflight_heartbeat(queue_case):
    case = queue_case
    event = create_event(case)
    enqueue(case, [event])
    started, release = threading.Event(), threading.Event()

    def slow_extract():
        started.set()
        release.wait(5)

    case.provider.extract_hook = slow_extract
    case.worker.heartbeat_seconds = 0.01
    thread = threading.Thread(target=case.worker.run_once)
    thread.start()
    try:
        assert started.wait(2)
        deadline = time.monotonic() + 2
        row = None
        while time.monotonic() < deadline:
            with case.db.transaction() as conn:
                row = conn.execute(
                    "SELECT state,inflight_count FROM worker_heartbeats WHERE worker_id=?",
                    (case.worker.worker_id,),
                ).fetchone()
            if row and row["state"] == "active" and row["inflight_count"] == 1:
                break
            time.sleep(0.02)
        assert row is not None, "慢 OCR 未写心跳"
        assert row["state"] == "active" and row["inflight_count"] == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    with case.db.transaction() as conn:
        row = conn.execute(
            "SELECT state,inflight_count FROM worker_heartbeats WHERE worker_id=?",
            (case.worker.worker_id,),
        ).fetchone()
    assert row["state"] == "idle" and row["inflight_count"] == 0


def test_persistent_slow_ocr_does_not_block_another_company_assistant(
    queue_case, chat_case
):
    case = queue_case
    event = create_event(case)
    enqueue(case, [event])
    started, release = threading.Event(), threading.Event()

    def slow_extract():
        started.set()
        release.wait(5)

    case.provider.extract_hook = slow_extract
    b_company = chat_case.other_company
    actor = chat_case.manager
    conversation = chat_case.chat_service.create(
        actor,
        ConversationCreate(
            company_id=b_company["id"],
            title="B 企业并发助手",
            idempotency_key=uuid4().hex,
        ),
    )["conversation"]
    turn = chat_case.chat_service.enqueue(
        actor,
        conversation["id"],
        TurnCreate(
            question="B 企业当前资料是否完整？",
            expected_scope_version=1,
            idempotency_key=uuid4().hex,
        ),
    )["turn"]
    unified = UnifiedWorker(
        assistant_worker=case.chat_worker,
        queue_worker=case.worker,
        idle_seconds=0.05,
    )
    unified.start()
    try:
        assert started.wait(3), "OCR 未开始"
        deadline = time.monotonic() + 5
        status = None
        while time.monotonic() < deadline:
            status = case.chat_service.turn(actor, conversation["id"], turn["id"])["turn"]["status"]
            if status == "succeeded":
                break
            time.sleep(0.02)
        assert status == "succeeded", f"助手未在 OCR 释放前完成：{status}"
        assert not release.is_set()
    finally:
        release.set()
        unified.stop()
