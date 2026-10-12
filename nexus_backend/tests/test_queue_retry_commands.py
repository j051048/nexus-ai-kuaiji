"""A repeated batch command cannot pick up failures that occurred after it was accepted."""
import pytest

from app.accounting.domain import AccountingError
from app.intelligence.provider import ProviderError
from test_queue_workflow import queue_case, ai_case, create_event, enqueue


def test_batch_retry_receipt_freezes_the_accepted_command_not_future_failures(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case), create_event(case)])
    case.provider.failure = ProviderError("schema_invalid", "合成结构化响应不合规")
    assert case.worker.run_once()
    first = case.queue.retry_batch(case.actors["employee"], batch["id"], "batch-command-exactly-once")
    assert first["progress"]["pending"] == 2
    assert case.worker.run_once() and case.worker.run_once()
    replayed = case.queue.retry_batch(case.actors["employee"], batch["id"], "batch-command-exactly-once")
    assert replayed["progress"]["failed"] == 2 and replayed["progress"]["pending"] == 0
    assert not case.worker.run_once() and case.provider.extract_calls == 3
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ocr_batch_retries").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ocr_retries").fetchone()[0] == 1


def test_no_failed_work_is_rejected_without_recording_a_command(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case)])
    with pytest.raises(AccountingError, match="没有可重试"):
        case.queue.retry_batch(case.actors["employee"], batch["id"], "no-failure-yet-command")
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ocr_batch_retries").fetchone()[0] == 0
    case.provider.failure = ProviderError("schema_invalid", "合成失败")
    assert case.worker.run_once()
    accepted = case.queue.retry_batch(case.actors["employee"], batch["id"], "no-failure-yet-command")
    assert accepted["progress"]["pending"] == 1
