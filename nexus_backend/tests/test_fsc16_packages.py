from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError
from app.tax.packages import (
    PackageConsume,
    PackageContractDraft,
    PackageRelease,
    RepairPackageService,
)
from test_business import business  # noqa: F401


def _draft(key="PKG-2026-001", vehicles=("V1", "V2")) -> PackageContractDraft:
    return PackageContractDraft(
        contract_no=key,
        coverage_scope="出租车基础保养包干（不含轮胎与事故维修）",
        vehicle_external_ids=list(vehicles),
        valid_from="2026-01",
        valid_to="2026-12",
        limit_cents=10000,
        policy="contract_cumulative",
        reason="客户核定年度包干合同",
    )


def test_package_contract_is_versioned_and_cumulative_across_periods(business):
    case = business
    service = RepairPackageService(case.db)
    first = service.create_draft(case.manager, case.book["id"], _draft())
    with pytest.raises(AccountingError) as denied:
        service.confirm(case.manager, case.book["id"], first["id"], 1)
    assert denied.value.status == 409
    service.confirm(case.actors["accountant"], case.book["id"], first["id"], 1)
    active = service.activate(case.manager, case.book["id"], first["id"], 1)
    assert active["status"] == "active" and active["version"] == 1

    service.consume(
        case.manager,
        case.book["id"],
        first["id"],
        PackageConsume(
            source_key="FP-A", source_version=1, period="2026-10",
            amount_cents=6000, idempotency_key="consume-a",
        ),
    )
    again = service.consume(
        case.manager,
        case.book["id"],
        first["id"],
        PackageConsume(
            source_key="FP-A", source_version=1, period="2026-10",
            amount_cents=6000, idempotency_key="consume-a",
        ),
    )
    assert again["replayed"] is True
    with pytest.raises(AccountingError) as over:
        service.consume(
            case.manager,
            case.book["id"],
            first["id"],
            PackageConsume(
                source_key="FP-B", source_version=1, period="2026-11",
                amount_cents=6000, idempotency_key="consume-b",
            ),
        )
    assert over.value.status == 409
    summary = service.summary(case.manager, case.book["id"], first["id"])
    assert summary["limit_cents"] == 10000
    assert summary["consumed_cents"] == 6000
    assert summary["remaining_cents"] == 4000
    with pytest.raises(AccountingError) as cross:
        service.summary(case.manager, "other-book", first["id"])
    assert cross.value.status == 404


def test_package_usage_release_is_bounded_and_idempotent(business):
    case = business
    service = RepairPackageService(case.db)
    contract = service.create_draft(case.manager, case.book["id"], _draft("PKG-2026-002"))
    service.confirm(case.actors["accountant"], case.book["id"], contract["id"], 1)
    service.activate(case.manager, case.book["id"], contract["id"], 1)
    consumed = service.consume(
        case.manager,
        case.book["id"],
        contract["id"],
        PackageConsume(
            source_key="FP-C", source_version=1, period="2026-10",
            amount_cents=5000, idempotency_key="consume-c",
        ),
    )
    release = service.release(
        case.manager,
        case.book["id"],
        consumed["id"],
        PackageRelease(amount_cents=3000, reason="客户确认部分项目未执行", idempotency_key="release-c"),
    )
    assert release["replayed"] is False
    replay = service.release(
        case.manager,
        case.book["id"],
        consumed["id"],
        PackageRelease(amount_cents=3000, reason="客户确认部分项目未执行", idempotency_key="release-c"),
    )
    assert replay["replayed"] is True
    with pytest.raises(AccountingError) as over:
        service.release(
            case.manager,
            case.book["id"],
            consumed["id"],
            PackageRelease(amount_cents=3000, reason="重复释放", idempotency_key="release-d"),
        )
    assert over.value.status == 409
    summary = service.summary(case.manager, case.book["id"], contract["id"])
    assert summary["consumed_cents"] == 2000
    assert summary["remaining_cents"] == 8000
