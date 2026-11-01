import pytest

from app.accounting.domain import AccountingError
from app.batch_ops.models import CreateMonthlyOpenBatch
from app.batch_ops.service import BatchOperationsService
from app.batch_ops.worker import BatchWorker
from test_monthly_batch import monthly_batch
from test_business import business


def test_monthly_open_partial_failure_retries_only_failed_item(monthly_batch):
    c = monthly_batch
    service = BatchOperationsService(c.db, c.monthly_batch)
    created = service.create_monthly_open(
        c.manager,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="awaiting",
            reason="部分失败回归",
            idempotency_key="batch-monthly-partial-0001",
        ),
    )
    calls = {"count": 0}
    def execute(item):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"status": "created", "book_id": "synthetic-book"}
        from app.accounting.domain import AccountingError
        raise AccountingError("合成第二户被阻断", 409)
    service.execute = execute
    worker = BatchWorker(service, worker_id="batch-partial")
    assert worker.run_once() and worker.run_once()
    finished = service.get(c.manager, created["id"])
    assert finished["succeeded"] == 1
    assert finished["failed"] == 1
    assert finished["progress"]["completed"] == 2
    assert sorted(item["status"] for item in finished["items"]) == ["failed", "succeeded"]
    retry = service.retry(c.manager, created["id"])
    assert retry["failed"] == 1
    assert sum(1 for item in retry["items"] if item["status"] == "queued") == 1


def test_monthly_open_batch_is_durable_idempotent_and_retryable(monthly_batch):
    c = monthly_batch
    service = BatchOperationsService(c.db, c.monthly_batch)
    request = CreateMonthlyOpenBatch(
        period="2026-11",
        scope="awaiting",
        reason="异步批量开期回归",
        idempotency_key="batch-monthly-open-0001",
    )
    created = service.create_monthly_open(c.manager, request)
    assert created["status"] == "queued"
    assert created["progress"] == {"total": 2, "completed": 0, "percent": 0}
    assert [item["status"] for item in created["items"]] == ["queued", "queued"]
    same = service.create_monthly_open(c.manager, request)
    assert same["id"] == created["id"]

    worker = BatchWorker(service, worker_id="batch-test")
    assert worker.run_once() is True
    assert worker.run_once() is True
    assert worker.run_once() is False

    finished = service.get(c.manager, created["id"])
    assert finished["status"] == "completed"
    assert finished["succeeded"] == 2
    assert finished["failed"] == 0
    assert finished["progress"]["percent"] == 100
    assert all(item["status"] == "succeeded" for item in finished["items"])
    assert all(item["result"]["book_id"] for item in finished["items"])

    # DCP04：没有失败项时重试必须是显式冲突，不能静默重排或复活已终态项。
    with pytest.raises(AccountingError, match="没有可重试"):
        service.retry(c.manager, created["id"])
    finished = service.get(c.manager, created["id"])
    assert finished["status"] == "completed"
    assert finished["succeeded"] == 2
