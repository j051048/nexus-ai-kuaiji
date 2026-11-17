import threading
import time

from app.batch_ops.models import CreateMonthlyOpenBatch
from app.batch_ops.service import BatchOperationsService
from app.batch_ops.worker import BatchWorker
from test_business import business  # noqa: F401
from test_monthly_batch import monthly_batch  # noqa: F401


def _created(c, key):
    service = BatchOperationsService(c.db, c.monthly_batch)
    batch = service.create_monthly_open(
        c.manager,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="awaiting",
            reason="租约续租回归",
            idempotency_key=key,
        ),
    )
    return service, batch


def test_long_batch_is_renewed_and_not_reclaimed(monthly_batch):
    c = monthly_batch
    service, batch = _created(c, "opt05-renew-long-0001")
    started = threading.Event()

    def execute(item):
        started.set()
        time.sleep(3.0)
        return {"status": "created", "book_id": "synthetic:" + item["company_id"]}

    service.execute = execute
    worker = BatchWorker(service, worker_id="renewer", lease_seconds=2, renew_interval=0.4)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert started.wait(2.0)
    time.sleep(2.2)
    assert service.recover() == 0, "续租后旧租约不应被恢复逻辑再次认领"
    thread.join(5.0)
    assert not thread.is_alive()

    row = (
        c.db.connect()
        .execute(
            "SELECT attempt,status,lease_token FROM batch_operation_items "
            "WHERE batch_id=? ORDER BY attempt DESC,sequence_no LIMIT 1",
            (batch["id"],),
        )
        .fetchone()
    )
    assert row["attempt"] == 1
    assert row["status"] == "succeeded"
    assert row["lease_token"] is None


def test_lost_renewal_stops_result_submission(monthly_batch):
    c = monthly_batch
    service, batch = _created(c, "opt05-renew-lost-0001")
    finished = []

    def execute(item):
        time.sleep(0.2)
        return {"status": "created", "book_id": "synthetic:" + item["company_id"]}

    service.execute = execute
    service.renew = lambda item, lease_seconds=120: False
    service.finish = lambda *args, **kwargs: finished.append(args)
    worker = BatchWorker(service, worker_id="losing", lease_seconds=2, renew_interval=0.05)
    assert worker.run_once() is True
    assert finished == [], "失去租约后不得提交或改写结果"
    row = (
        c.db.connect()
        .execute(
            "SELECT status,lease_token FROM batch_operation_items WHERE batch_id=? "
            "AND status='running'",
            (batch["id"],),
        )
        .fetchone()
    )
    assert row is not None
    assert row["lease_token"]
