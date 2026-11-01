"""DCP04：重试不能清零尝试次数、不能复活取消项；取消/跳过要有原因与意图。"""

import pytest

from app.accounting.domain import AccountingError
from app.batch_ops.models import CreateMonthlyOpenBatch
from app.batch_ops.service import BatchOperationsService
from app.batch_ops.worker import BatchWorker
from test_business import business  # noqa: F401  (fixture)
from test_monthly_batch import monthly_batch  # noqa: F401  (fixture)


def _service(case):
    return BatchOperationsService(case.db, case.monthly_batch)


def _create(service, case, key="dcp04-batch-0001"):
    return service.create_monthly_open(
        case.manager,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="awaiting",
            reason="DCP04 批量控制回归",
            idempotency_key=key,
        ),
    )


def _fail_all(service):
    def execute(item):
        raise AccountingError("合成户被阻断", 409)

    service.execute = execute


def test_retry_keeps_attempts_and_never_revives_cancelled(monthly_batch):  # noqa: F811
    case = monthly_batch
    service = _service(case)
    created = _create(service, case)
    items = {item["company_id"]: item for item in created["items"]}
    cancelled_company = created["items"][1]["company_id"]

    result = service.cancel_item(
        case.manager, created["id"], items[cancelled_company]["id"], "客户本月停业"
    )
    assert result["status"] == "cancelled"
    assert result["cancel_reason"] == "客户本月停业"

    _fail_all(service)
    worker = BatchWorker(service, worker_id="dcp04-worker")
    for _ in range(4):
        worker.run_once()
        # 失败重试有 30 秒退避；测试把它拨回过去，观察真实的重试上限。
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE batch_operation_items SET available_at=? WHERE status='retry_wait'",
                ("2000-01-01T00:00:00Z",),
            )

    failed = [i for i in service.get(case.manager, created["id"])["items"] if i["status"] == "failed"]
    assert failed, "第二户应当重试到上限后失败"
    attempts_before = failed[0]["attempt"]

    after = service.retry(case.manager, created["id"])
    requeued = {
        i["company_id"]: i for i in after["items"] if i["company_id"] == failed[0]["company_id"]
    }
    item = list(requeued.values())[0]
    assert item["status"] == "queued"
    assert item["attempt"] == attempts_before, "重试不能把 attempt 清零"
    assert item["retry_count"] == 1

    cancelled = [i for i in after["items"] if i["company_id"] == cancelled_company][0]
    assert cancelled["status"] == "cancelled", "重试不能复活已取消项"
    assert cancelled["attempt"] == 0


def test_retry_without_failed_items_is_rejected(monthly_batch):  # noqa: F811
    case = monthly_batch
    service = _service(case)
    created = _create(service, case, key="dcp04-batch-0002")
    for item in created["items"]:
        service.cancel_item(case.manager, created["id"], item["id"], "整批取消")
    with pytest.raises(AccountingError, match="没有可重试"):
        service.retry(case.manager, created["id"])


def test_cancel_running_item_records_intent_and_keeps_committed_result(monthly_batch):  # noqa: F811
    case = monthly_batch
    service = _service(case)
    created = _create(service, case, key="dcp04-batch-0003")
    claimed = service.claim("dcp04-worker")
    assert claimed and claimed["batch_id"] == created["id"]
    intent = service.cancel_item(case.manager, created["id"], claimed["item_id"], "客户临时撤回")
    assert intent["status"] == "running"
    assert intent["cancel_requested_at"]
    assert intent["cancel_reason"] == "客户临时撤回"

    assert service.finish(claimed, "succeeded", {"status": "created", "book_id": "kept"}) is True
    item = [
        i
        for i in service.get(case.manager, created["id"])["items"]
        if i["id"] == claimed["item_id"]
    ][0]
    assert item["status"] == "succeeded", "已提交的业务结果不能被改成未做"
    assert item["cancel_requested"] is True


def test_skip_and_cancel_require_reason_and_terminal_items_are_rejected(monthly_batch):  # noqa: F811
    case = monthly_batch
    service = _service(case)
    created = _create(service, case, key="dcp04-batch-0004")
    first, second = created["items"][0]["id"], created["items"][1]["id"]
    with pytest.raises(AccountingError):
        service.skip_item(case.manager, created["id"], first, " ")
    skipped = service.skip_item(case.manager, created["id"], first, "本月无流水")
    assert skipped["status"] == "skipped"
    assert skipped["skip_reason"] == "本月无流水"
    with pytest.raises(AccountingError, match="不能取消"):
        service.cancel_item(case.manager, created["id"], first, "太晚了已经跳过")
    assert service.skip_item(case.manager, created["id"], second, "不需要").get("status") == "skipped"


def test_failure_csv_escapes_formula_and_lists_only_authorized_items(monthly_batch):  # noqa: F811
    case = monthly_batch
    service = _service(case)
    created = _create(service, case, key="dcp04-batch-0005")
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE batch_operation_items SET company_name=? WHERE id=?",
            ("=1+1@evil", created["items"][0]["id"]),
        )
    service.cancel_item(case.manager, created["id"], created["items"][0]["id"], "客户停业")
    csv_text = service.failure_csv(case.manager, created["id"])
    assert "'=1+1@evil" in csv_text
    assert "客户停业" in csv_text
    assert "@evil" in csv_text
    with pytest.raises(AccountingError, match="不存在或无权访问"):
        service.failure_csv(case.manager, "missing-batch")
