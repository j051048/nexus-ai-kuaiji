"""配额真正接在导入链路上，而不是只存在于 QuotaService 里。"""

import pytest
from app.accounting.database import Database
from app.accounting.models import BookCreate, ImportRequest, SourceRow
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingError, AccountingService
from app.security.quotas import QuotaService


@pytest.fixture()
def case(tmp_path):
    service = AccountingService(Database(tmp_path / "quota.db"))
    bootstrap(service, seed_examples=False)
    book = service.create_book(LOCAL_ACTOR, BookCreate(name="配额测试", period="2026-10"))["id"]
    return service, book


def _row(index):
    return SourceRow(
        external_id=f"quota-{index}",
        date="2026-10-01",
        description="办公用品",
        amount="1.00",
        direction="expense",
    )


def test_import_over_the_firm_limit_is_refused_before_anything_is_written(case):
    service, book = case
    QuotaService(service.db).configure(LOCAL_ACTOR, import_rows_per_batch=2)

    with pytest.raises(AccountingError) as caught:
        service.import_sources(
            LOCAL_ACTOR,
            book,
            ImportRequest(source_name="三行", rows=[_row(1), _row(2), _row(3)]),
        )

    assert caught.value.status == 413
    assert "超过本单位上限" in str(caught.value)
    # 拒绝发生在写入前：账套里不该留下半批流水。
    assert service.get_book(LOCAL_ACTOR, book)["sources"] == []


def test_import_within_the_limit_still_lands(case):
    service, book = case
    QuotaService(service.db).configure(LOCAL_ACTOR, import_rows_per_batch=2)

    result = service.import_sources(
        LOCAL_ACTOR, book, ImportRequest(source_name="两行", rows=[_row(1), _row(2)])
    )

    assert result["imported"] == 2
    assert len(service.get_book(LOCAL_ACTOR, book)["sources"]) == 2
