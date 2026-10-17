from app.batch_ops.models import CreateMonthlyOpenBatch
from app.batch_ops.service import BatchOperationsService
from app.batch_ops.worker import BatchWorker
from test_monthly_batch import monthly_batch
from test_business import business


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

    retry = service.retry(c.manager, created["id"])
    assert retry["status"] == "completed"
    assert retry["succeeded"] == 2
