"""Batch 07：异步任务、在途并发与原件容量三条配额接线。

口径沿用导入链路：**在同一个写事务里核验**，超限在 INSERT 之前抛错，
因此不会留下半批任务、半条上传意图或脏数据；拒绝只写结构化告警日志。
"""

import inspect
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from app.accounting.domain import AccountingError
from app.jobs.service import QueueService
from app.security.quotas import QuotaService
from app.uploads import UploadCreate, UploadIntentService
from test_business import business  # noqa: F401


def _queue(case) -> QueueService:
    """真实 QueueService，intelligence 只做 access/business 的载体（本测试不走模型）。"""
    return QueueService(case.db, SimpleNamespace(business=case.service, access=case.service.access))


def _uploads(case) -> UploadIntentService:
    # 无 sign_upload 的桩存储：create() 只写意图行，不落任何字节。
    return UploadIntentService(case.db, SimpleNamespace(backend=SimpleNamespace()))


@pytest.fixture
def counters(monkeypatch):
    """把「今日已入队」与「在途任务」两个计数器替换成固定值。

    真实计数 SQL 是 `ocr_jobs` 上的两条聚合查询（`_queued_today` / `_in_flight`），
    这里只替换计数来源、不替换守卫本身：429 判定与文案仍由生产代码执行。
    """
    state = {"queued_today": 0, "in_flight": 0}
    monkeypatch.setattr(
        QueueService, "_queued_today", staticmethod(lambda conn, firm_id: state["queued_today"])
    )
    monkeypatch.setattr(
        QueueService, "_in_flight", staticmethod(lambda conn, firm_id: state["in_flight"])
    )
    return state


def test_enqueue_calls_the_quota_guard_before_writing_anything():
    source = inspect.getsource(QueueService.enqueue_in_transaction)
    assert "_quota_checks(conn, actor, len(inputs))" in source
    # 配额核验必须早于批次 INSERT：拒绝时不能留下半批数据。
    assert source.index("_quota_checks") < source.index("INSERT INTO ocr_batches")


def test_daily_job_quota_refuses_at_the_boundary_and_allows_at_limit(business, counters):
    case = business
    queue = _queue(case)
    quota = QuotaService(case.db)
    quota.configure(case.manager, async_jobs_per_day=3)
    counters["queued_today"] = 3
    with case.db.transaction(actor=case.manager) as conn:
        with pytest.raises(AccountingError) as refused:
            queue._quota_checks(conn, case.manager, 1)
    assert refused.value.status == 429
    assert "单日异步任务数" in refused.value.detail or "异步任务" in refused.value.detail
    quota.configure(case.manager, async_jobs_per_day=4)
    with case.db.transaction(actor=case.manager) as conn:
        queue._quota_checks(conn, case.manager, 1)  # 等于上限仍放行


def test_in_flight_concurrency_quota_is_independent_of_daily_count(business, counters):
    case = business
    queue = _queue(case)
    quota = QuotaService(case.db)
    quota.configure(case.manager, async_jobs_per_day=100, concurrent_requests=2)
    counters["in_flight"] = 2
    with case.db.transaction(actor=case.manager) as conn:
        with pytest.raises(AccountingError) as refused:
            queue._quota_checks(conn, case.manager, 1)
    assert refused.value.status == 429
    assert "在途任务" in refused.value.detail
    # 只放宽并发配额即可放行（证明两条配额各自独立生效）。
    quota.configure(case.manager, concurrent_requests=3)
    with case.db.transaction(actor=case.manager) as conn:
        queue._quota_checks(conn, case.manager, 1)


def test_quota_counters_are_real_aggregates_over_ocr_jobs(business):
    """计数器本身是 `ocr_jobs` 上的真实聚合（空企业为 0），不是常量。"""
    case = business
    queue = _queue(case)
    with case.db.transaction(actor=case.manager) as conn:
        assert queue._queued_today(conn, case.manager.firm_id) == 0
        assert queue._in_flight(conn, case.manager.firm_id) == 0
        assert "FROM ocr_jobs" in inspect.getsource(QueueService._queued_today)
        assert "FROM ocr_jobs" in inspect.getsource(QueueService._in_flight)


def test_upload_storage_quota_refuses_without_leaving_an_intent(business):
    case = business
    quota = QuotaService(case.db)
    quota.configure(case.manager, storage_bytes=1048576)
    uploads = _uploads(case)
    payload = UploadCreate(
        purpose="invoice_import",
        filename="原件.pdf",
        media_type="application/pdf",
        size_bytes=2 * 1048576,
        sha256="b" * 64,
    )
    with pytest.raises(AccountingError) as refused:
        uploads.create(case.manager, payload)
    assert refused.value.status == 413
    assert "原件容量" in refused.value.detail
    with case.db.transaction(actor=case.manager) as conn:
        assert uploads._stored_bytes(conn, case.manager.firm_id) == 0
        assert conn.execute("SELECT COUNT(*) FROM upload_intents").fetchone()[0] == 0


def test_upload_storage_quota_allows_within_the_limit_and_counts_usage(business):
    case = business
    QuotaService(case.db).configure(case.manager, storage_bytes=1048576)
    uploads = _uploads(case)
    created = uploads.create(
        case.manager,
        UploadCreate(
            purpose="invoice_import",
            filename="小票.pdf",
            media_type="application/pdf",
            size_bytes=1024,
            sha256="c" * 64,
        ),
    )
    assert created["id"] and created["max_bytes"] > 0
    with case.db.transaction(actor=case.manager) as conn:
        assert uploads._stored_bytes(conn, case.manager.firm_id) == 1024
    # 已丢弃的上传意图不计入在用存储容量。
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute("UPDATE upload_intents SET state='discarded' WHERE id=?", (created["id"],))
        assert uploads._stored_bytes(conn, case.manager.firm_id) == 0
