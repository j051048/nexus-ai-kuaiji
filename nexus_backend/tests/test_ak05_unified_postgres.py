"""AK05 unified lanes against an explicitly opted-in disposable local PG.

These are real PostgreSQL service/worker/thread tests with synthetic providers.
They complement the separate OS-process claim/crash tests; threads are not
reported as independent processes. No live model or remote database is used.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ExportRequest
from app.assistant.meter import AssistantMeter
from app.assistant.models import ConversationCreate, TurnCreate
from app.business.models import HandoffRequest
from app.intelligence.models import ProviderUsage
from app.jobs.worker import QueueWorker
from app.operations.meter import MeterService
from app.operations.pricing import Pricing, Rate
from app.unified_worker import UnifiedWorker
from test_stage15_mixed_queue_postgres import (
    ADMIN_URL,
    APP_URL,
    WORKER_URL,
    business_case,  # noqa: F401 -- imported pytest fixture dependencies
    business_postgres,  # noqa: F401
    chat_pg,  # noqa: F401
    mixed_pg,  # noqa: F401
    processing,
)


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1"
    or not ADMIN_URL
    or not APP_URL
    or not WORKER_URL,
    reason="requires an explicitly opted-in disposable AK05 local PostgreSQL",
)


@pytest.fixture(autouse=True)
def require_isolated_target(monkeypatch):
    targets = [urlsplit(value) for value in (ADMIN_URL, APP_URL, WORKER_URL)]
    for target in targets:
        assert target.hostname == "127.0.0.1", "AK05 requires a loopback database"
        assert target.port and target.port != 5432, "AK05 requires an isolated port"
        assert target.path.startswith("/ak01_local_ak05_"), "AK05 requires its disposable DB"
    assert len({(target.hostname, target.port, target.path) for target in targets}) == 1

    def deny_python_network(*_args, **_kwargs):
        raise AssertionError("AK05 synthetic providers must not open network connections")

    # libpq opens the explicitly checked PG connection in native code. Provider
    # Python sockets remain forbidden, including on UnifiedWorker threads.
    monkeypatch.setattr(socket.socket, "connect", deny_python_network)
    monkeypatch.setattr(socket, "create_connection", deny_python_network)


def admin_rows(case, sql, parameters=()):
    with case.raw.psycopg.connect(ADMIN_URL) as conn:
        return conn.execute(sql, parameters).fetchall()


def wait_for(check, *, timeout=15, detail="condition did not complete"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(detail)


def configured_workers(case):
    pricing = Pricing(
        ocr=Rate("2.000000", "4.000000"),
        proposal=Rate("2.000000", "4.000000"),
        chat=Rate("2.000000", "4.000000"),
    )
    case.provider.usage = ProviderUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    case.worker_queue.intelligence.meter = MeterService(case.worker_queue.db, pricing=pricing)
    queue = QueueWorker(case.worker_queue, worker_id="ak05-pg-queue-" + uuid4().hex)
    queue.heartbeat_seconds = 0.05
    case.chat.worker.meter = AssistantMeter(
        case.chat.worker.db, MeterService(case.chat.worker.db, pricing=pricing)
    )
    unified = UnifiedWorker(
        queue_worker=queue,
        assistant_worker=case.chat.worker,
        idle_seconds=0.05,
    )
    unified.monitor_interval = 0.05
    return queue, unified


def heartbeat_pair(case, queue):
    rows = admin_rows(
        case,
        "SELECT 0 AS lane,heartbeat_at,state,inflight_count FROM accounting.worker_heartbeats "
        "WHERE worker_id=%s UNION ALL "
        "SELECT 1 AS lane,heartbeat_at,state,inflight_count FROM accounting.assistant_worker_heartbeats "
        "WHERE worker_id=%s ORDER BY lane",
        (queue.worker_id, case.chat.worker.worker_id),
    )
    return [row[1:] for row in rows]


def assert_exact_cost(case, run_id):
    rows = admin_rows(
        case,
        "SELECT state,estimated_micro,input_tokens,output_tokens "
        "FROM accounting.model_costs WHERE id=%s",
        (run_id,),
    )
    assert rows == [("estimated", 16, 2, 3)]


def test_pg_late_assistant_overlaps_slow_ocr_and_drains_before_database_close(mixed_pg):
    case = mixed_pg
    first, second, third = case.companies
    job_id = processing(case, first, "ocr")
    queue, unified = configured_workers(case)
    ocr_entered, ocr_release, ocr_exited = (threading.Event() for _ in range(3))
    chat_entered, chat_release = threading.Event(), threading.Event()

    def slow_ocr():
        ocr_entered.set()
        try:
            assert ocr_release.wait(30), "synthetic OCR guard expired"
        finally:
            ocr_exited.set()

    def slow_chat():
        chat_entered.set()
        assert chat_release.wait(30), "synthetic assistant guard expired"

    case.provider.extract_hook = slow_ocr
    case.chat.provider.hook = slow_chat
    unified.start()
    try:
        assert ocr_entered.wait(15), "OCR must enter before the assistant is enqueued"
        conversation = case.chat.service.create(
            case.raw.owner,
            ConversationCreate(
                company_id=second.company,
                book_ids=[second.book],
                title="AK05 后到企业助手",
                idempotency_key=uuid4().hex,
            ),
        )["conversation"]
        turn = case.chat.service.enqueue(
            case.raw.owner,
            conversation["id"],
            TurnCreate(
                question="仅核对本企业当前账套资料。",
                expected_scope_version=1,
                idempotency_key=uuid4().hex,
            ),
        )["turn"]
        assert chat_entered.wait(15), "a blocked OCR must not prevent the other company"
        assert not ocr_exited.is_set()
        running = admin_rows(
            case,
            "SELECT company_id FROM accounting.ocr_jobs WHERE id=%s AND status='running' "
            "UNION ALL SELECT company_id FROM accounting.assistant_turns "
            "WHERE id=%s AND status='running'",
            (job_id, turn["id"]),
        )
        assert {row[0] for row in running} == {first.company, second.company}
        assert queue.inflight_count() == case.chat.worker.inflight_count() == 1

        samples = [set(), set()]

        def two_durable_heartbeats():
            rows = heartbeat_pair(case, queue)
            if len(rows) == 2:
                for index, row in enumerate(rows):
                    if row[1:] == ("active", 1):
                        samples[index].add(row[0])
            return all(len(stamps) >= 2 for stamps in samples)

        wait_for(two_durable_heartbeats, detail="both live lanes must advance PG heartbeats")
        chat_release.set()
        wait_for(
            lambda: (
                admin_rows(
                    case,
                    "SELECT status FROM accounting.assistant_turns WHERE id=%s",
                    (turn["id"],),
                )
                == [("succeeded",)]
            ),
            detail="late assistant must finish while OCR remains blocked",
        )
        assert not ocr_exited.is_set()
        assert_exact_cost(case, turn["id"])
        with pytest.raises(AccountingError) as hidden:
            case.chat.service.turn(first.applicant, conversation["id"], turn["id"])
        assert hidden.value.status == 404

        unified.stop()
        assert not unified.wait_for_idle(0.05)
        assert not queue.queue.db._closed
        with queue.queue.worker_transaction() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        # Enqueue after stop: shutdown may finish A but must not claim C.
        queued_after_stop = processing(case, third, "ocr")
        stopped_stamps = set()

        def draining_heartbeats_advance():
            rows = heartbeat_pair(case, queue)
            if len(rows) == 2 and rows[0][1:] == ("active", 1):
                stopped_stamps.add(rows[0][0])
            return len(stopped_stamps) >= 2

        wait_for(draining_heartbeats_advance, detail="draining OCR must keep its PG heartbeat")
        ocr_release.set()
        assert unified.wait_for_idle(15), "all task and monitor DB work must drain"
        assert queue.inflight_count() == case.chat.worker.inflight_count() == 0
        assert [row[1:] for row in heartbeat_pair(case, queue)] == [
            ("stopped", 0),
            ("stopped", 0),
        ]
        task = admin_rows(
            case,
            "SELECT status,current_run_id,event_id FROM accounting.ocr_jobs WHERE id=%s",
            (job_id,),
        )[0]
        assert task[0] == "succeeded"
        assert_exact_cost(case, task[1])
        assert admin_rows(
            case,
            "SELECT status,attempt_count FROM accounting.ocr_jobs WHERE id=%s",
            (queued_after_stop,),
        ) == [("queued", 0)]
        assert case.provider.extract_calls == case.chat.provider.calls == 1
        assert admin_rows(
            case, "SELECT count(*) FROM accounting.ocr_attempts WHERE job_id=%s", (job_id,)
        ) == [(1,)]

        event = case.intelligence.business.get_event(first.applicant, task[2])
        with pytest.raises(AccountingError) as hidden_event:
            case.intelligence.business.get_event(second.applicant, task[2])
        assert hidden_event.value.status == 404
        event = case.intelligence.business.submit(first.applicant, event["id"], event["version"])
        event = case.intelligence.business.approve_department(
            first.approver, event["id"], event["version"]
        )
        event = case.intelligence.business.approve_finance(
            first.finance, event["id"], event["version"]
        )
        handoff = case.intelligence.business.handoff(
            first.finance,
            event["id"],
            HandoffRequest(
                expected_version=event["version"], book_id=first.book, idempotency_key=uuid4().hex
            ),
        )
        with pytest.raises(AccountingError) as unreviewed:
            case.raw.service.export(
                case.raw.owner,
                first.book,
                ExportRequest(draft_ids=[handoff["draft_id"]], idempotency_key=uuid4().hex),
            )
        assert unreviewed.value.status == 409
        assert "已审核" in unreviewed.value.detail
        # Closing is deliberately after a positive drain. Do not merely check
        # a stub close counter: the real PG pool must reject new acquisitions.
        queue.queue.db.close()
        with pytest.raises(RuntimeError):
            queue.queue.db.connect()
    finally:
        chat_release.set()
        ocr_release.set()
        unified.stop()
        assert unified.wait_for_idle(15), "test must leave no active DB-owning threads"


@pytest.mark.parametrize("failure_at", ["before_claim", "after_business_commit"])
def test_pg_maintenance_failure_does_not_strand_claim_or_replay(mixed_pg, monkeypatch, failure_at):
    case = mixed_pg
    job_id = processing(case, case.companies[0], "ocr")
    queue, unified = configured_workers(case)
    original = queue.maintenance
    calls = []

    def failing_maintenance(*args, **kwargs):
        calls.append(bool(case.provider.extract_calls))
        should_fail = (not calls[-1]) if failure_at == "before_claim" else calls[-1]
        if should_fail:
            raise RuntimeError("synthetic bounded maintenance outage")
        return original(*args, **kwargs)

    monkeypatch.setattr(queue, "maintenance", failing_maintenance)
    unified.start()
    try:
        task = wait_for(
            lambda: next(
                (
                    row
                    for row in admin_rows(
                        case,
                        "SELECT status,current_run_id FROM accounting.ocr_jobs WHERE id=%s",
                        (job_id,),
                    )
                    if row[0] == "succeeded"
                ),
                None,
            ),
            detail="maintenance failure must not strand a real PG claim",
        )
        assert_exact_cost(case, task[1])
        unified.stop()
        assert unified.wait_for_idle(15)
        assert (False if failure_at == "before_claim" else True) in calls
        assert case.provider.extract_calls == 1
        assert admin_rows(
            case, "SELECT count(*) FROM accounting.ocr_attempts WHERE job_id=%s", (job_id,)
        ) == [(1,)]
        assert not queue.queue.db._closed
    finally:
        unified.stop()
        assert unified.wait_for_idle(15)
