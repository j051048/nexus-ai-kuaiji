from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import EntityCreate, TransportAssignmentCreate, TransportRelationCreate, TransportSubjectCreate
from app.tax.fees import TaxFeeService
from app.ledgers.service import LedgersService
from app.tax.sources import TaxSourceAdapter
from test_business import business  # noqa: F401
from test_tax04_sources import xlsx

COLS = {
    "external_id": "行ID", "date": "日期", "description": "摘要", "amount": "金额",
    "quantity": "数量", "target_id": "目标", "driver_external_id": "驾驶员编号",
    "vehicle_external_id": "车辆编号", "discount": "优惠减免", "adjustment": "调整",
    "adjustment_reason": "调整原因",
}
ROWS = [
    ["行ID", "日期", "摘要", "金额", "数量", "目标", "驾驶员编号", "车辆编号", "优惠减免", "调整", "调整原因"],
    ["L1", "2026-10-02", "机场接送", "100.00", "1", "", "D1", "V1", "10", "", ""],
    ["L2", "2026-10-02", "包车", "80.00", "2", "", "D2", "V2", "", "5", "季节性减免"],
    ["L3", "2026-10-02", "零额", "0", "0", "", "D1", "V1", "", "", ""],
    ["L4", "2026-10-05", "无班次", "50.00", "1", "", "D2", "V2", "", "", ""],
]


def _mapping(case, subject):
    ledger = LedgersService(case.db)
    entity = ledger.create_entity(case.manager, case.book["id"], EntityCreate(
        kind="counterparty" if subject["kind"] == "driver" else "project",
        code="TAXI-" + subject["external_id"], name=subject["display_name"] + "核算主体",
    ))
    ledger.create_transport_relation(case.manager, case.book["id"], TransportRelationCreate(
        subject_id=subject["id"], target_id=entity["id"], contract_external_id="C1",
        operating_right="合成规费测试已核定映射", valid_from="2026-10-01", valid_to=None,
    ))
    return entity


def _masters(case, *, with_mapping=True):
    ledger = LedgersService(case.db)
    driver = ledger.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="driver", external_id="D1", display_name="张师傅"))
    vehicle = ledger.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="V1", display_name="京A1"))
    driver2 = ledger.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="driver", external_id="D2", display_name="李师傅"))
    vehicle2 = ledger.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="V2", display_name="京A2"))
    if with_mapping:
        for subject in (driver, vehicle, driver2, vehicle2):
            _mapping(case, subject)
    for d, v in ((driver, vehicle), (driver2, vehicle2)):
        ledger.create_transport_assignment(case.manager, case.book["id"], TransportAssignmentCreate(
            vehicle_id=v["id"], driver_id=d["id"], shift="full", contract_external_id="C1",
            valid_from="2026-10-01", valid_to="2026-10-03",
        ))
    return driver, vehicle, driver2, vehicle2


def _source(case, rows=ROWS, version=1):
    return TaxSourceAdapter(case.db).import_source(
        case.manager, case.book["id"], filename="规费.xlsx",
        data=xlsx({"运输明细": rows}), source_system="客户运输台账", external_id="S-FEE",
        version=version, sheet_name="运输明细", column_map=COLS, rule_version="tax-source-v1",
    )


POLICY = {"version": "fee-v1", "driver_receivable_account": "1122", "vehicle_revenue_account": "6001"}


def test_fee_batch_builds_balanced_draft_and_requires_independent_review(business):
    case = business
    _driver, _vehicle, driver2, vehicle2 = _masters(case)
    LedgersService(case.db).create_transport_assignment(
        case.manager, case.book["id"], TransportAssignmentCreate(
            vehicle_id=vehicle2["id"], driver_id=driver2["id"], shift="full",
            contract_external_id="C1", valid_from="2026-10-05", valid_to="2026-10-05",
        ),
    )
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    assert batch["total_cents"] == 22500
    assert batch["row_count"] == 3 and batch["skipped_count"] == 0
    assert [(item["name"], item["net_cents"]) for item in batch["by_driver"]] == [
        ("张师傅", 9000),
        ("李师傅", 13500),
    ]
    assert [(item["name"], item["net_cents"]) for item in batch["by_vehicle"]] == [
        ("京A1", 9000),
        ("京A2", 13500),
    ]
    assert batch["source_files"] == [{
        "id": source["id"],
        "filename": "规费.xlsx",
        "external_id": "S-FEE",
        "version": 1,
        "sha256": source["sha256"],
    }]
    assert {r["status"] for r in batch["rows"]} == {"adopted"}
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert len(draft["lines"]) == 6
    assert sum(Decimal(line["debit"]) for line in draft["lines"]) == Decimal("225.00")
    assert sum(Decimal(line["credit"]) for line in draft["lines"]) == Decimal("225.00")
    assert draft["issues"] == []
    with pytest.raises(AccountingError):
        service.review(case.manager, case.book["id"], batch["id"], expected_version=1, reason="自己复核")
    reviewed = service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="独立复核")
    assert reviewed["status"] == "reviewed" and reviewed["version"] == 2
    assert reviewed["draft"]["status"] == "approved"
    with pytest.raises(AccountingError):
        service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="重复")


def test_fee_batch_with_rejected_rows_blocks_independent_review(business):
    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert any("拒绝行" in issue for issue in draft["issues"])
    with pytest.raises(AccountingError) as rejected:
        service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="仍有拒绝行")
    assert rejected.value.status == 422


def test_new_source_version_makes_unfrozen_batch_invalid_for_review(business):
    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    _source(case, version=2)
    with pytest.raises(AccountingError) as error:
        service.validate(case.manager, case.book["id"], batch["id"])
    assert error.value.status == 409
    invalid = service.invalidate(case.manager, case.book["id"], batch["id"], expected_version=1, reason="来源升级")
    assert invalid["status"] == "invalidated"
    with pytest.raises(AccountingError):
        service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=2, reason="失效")


def test_reviewed_batch_is_marked_stale_after_source_upgrade(business):
    case = business
    _driver, _vehicle, driver2, vehicle2 = _masters(case)
    LedgersService(case.db).create_transport_assignment(
        case.manager, case.book["id"], TransportAssignmentCreate(
            vehicle_id=vehicle2["id"], driver_id=driver2["id"], shift="full",
            contract_external_id="C1", valid_from="2026-10-05", valid_to="2026-10-05",
        ),
    )
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    reviewed = service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="独立复核")
    assert reviewed["status"] == "reviewed" and reviewed["stale"] is False
    _source(case, version=2)
    stale = service.get(case.manager, case.book["id"], batch["id"])
    assert stale["status"] == "reviewed"
    assert stale["stale"] is True
    assert "失效" in stale["stale_reason"]
    with pytest.raises(AccountingError) as error:
        service.validate(case.manager, case.book["id"], batch["id"])
    assert error.value.status == 409


def test_cross_period_source_never_generates_a_prior_month_batch(business):
    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)
    with pytest.raises(AccountingError) as failure:
        service.generate(
            case.manager, case.book["id"], period="2026-09",
            source_file_ids=[source["id"]], policy=POLICY,
        )
    assert failure.value.status == 409
    assert "期间" in failure.value.detail


def test_policy_and_master_gaps_are_rejected(business):
    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)
    with pytest.raises(AccountingError):
        service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy={})
    with pytest.raises(AccountingError):
        service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy={**POLICY, "driver_receivable_account": "6001"})


def test_draft_request_rejects_more_than_2400_lines():
    from pydantic import ValidationError

    from app.accounting.models import DraftLine, DraftUpdate

    line = DraftLine(account_code="1122", debit="1.00", credit="0.00")
    with pytest.raises(ValidationError):
        DraftUpdate(expected_version=1, description="超限", lines=[line] * 2401)
    assert len(DraftUpdate(expected_version=1, description="边界", lines=[line] * 2400).lines) == 2400


def test_batch_budget_rejects_more_than_2400_controlled_lines(business):
    case = business
    _masters(case)
    rows = [ROWS[0]] + [
        [f"L{index:04d}", "2026-10-02", "大额合成批次", "1.00", "1", "", "D1", "V1", "", "", ""]
        for index in range(1201)
    ]
    source = _source(case, rows=rows)
    with pytest.raises(AccountingError) as error:
        TaxFeeService(case.db).generate(
            case.manager, case.book["id"], period="2026-10",
            source_file_ids=[source["id"]], policy=POLICY,
        )
    assert error.value.status == 413


def test_fee_batch_zero_negative_unknown_and_source_reasons(business):
    case = business
    _masters(case)
    rows = [
        ROWS[0],
        ["L1", "2026-10-02", "正常", "100.00", "1", "", "D1", "V1", "10", "", ""],
        ["Z1", "2026-10-02", "零额", "100.00", "1", "", "D1", "V1", "100", "", ""],
        ["N1", "2026-10-02", "负额", "100.00", "1", "", "D1", "V1", "150", "", ""],
        ["U1", "2026-10-02", "未知驾驶员", "100.00", "1", "", "D9", "V1", "", "", ""],
        ["R1", "2026-10-02", "336/335差额", "336.00", "1", "", "D1", "V1", "", "-1", "336/335 差额一次调整"],
        ["M1", "2026-10-02", "缺调整理由", "50.00", "1", "", "D1", "V1", "", "5", ""],
    ]
    source = _source(case, rows=rows)
    batch = TaxFeeService(case.db).generate(
        case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY,
    )
    assert batch["total_cents"] == 42500
    assert batch["row_count"] == 6 and batch["skipped_count"] == 4
    status = {row["external_id"]: row["status"] for row in batch["rows"]}
    assert status == {"L1": "adopted", "Z1": "zero", "N1": "rejected", "U1": "rejected", "R1": "adopted", "M1": "rejected"}
    reasons = {row["external_id"]: row["reason"] for row in batch["rows"]}
    assert "负" in reasons["N1"] and "主档" in reasons["U1"] and "理由" in reasons["M1"]
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    amounts = sorted(Decimal(line["debit"]) for line in draft["lines"] if Decimal(line["debit"]) > 0)
    assert amounts == [Decimal("90.00"), Decimal("335.00")]


def test_fee_batch_accepts_two_drivers_on_one_vehicle_across_shifts(business):
    case = business
    ledger = LedgersService(case.db)
    driver1 = ledger.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="driver", external_id="D1", display_name="白班"))
    driver2 = ledger.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="driver", external_id="D2", display_name="夜班"))
    vehicle = ledger.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="V1", display_name="双班车"))
    for subject in (driver1, driver2, vehicle):
        _mapping(case, subject)
    for driver, shift in ((driver1, "day"), (driver2, "night")):
        ledger.create_transport_assignment(case.manager, case.book["id"], TransportAssignmentCreate(
            vehicle_id=vehicle["id"], driver_id=driver["id"], shift=shift, contract_external_id="C1",
            valid_from="2026-10-01", valid_to="2026-10-03",
        ))
    rows = [
        ROWS[0],
        ["L1", "2026-10-02", "白班", "100.00", "1", "", "D1", "V1", "", "", ""],
        ["L2", "2026-10-02", "夜班", "80.00", "1", "", "D2", "V1", "", "", ""],
    ]
    source = _source(case, rows=rows)
    batch = TaxFeeService(case.db).generate(
        case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY,
    )
    assert batch["total_cents"] == 18000
    assert [row["status"] for row in batch["rows"]] == ["adopted", "adopted"]
    assert len(case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])["lines"]) == 4


def test_cross_period_correction_requires_and_retains_source_reason(business):
    case = business
    _masters(case)
    rows = [
        ["行ID", "日期", "摘要", "金额", "数量", "目标", "驾驶员编号", "车辆编号", "优惠减免", "调整", "调整原因"],
        ["L1", "2026-10-02", "机场接送", "100.00", "1", "", "D1", "V1", "", "-20", "更正上期多计规费"],
    ]
    source = _source(case, rows=rows)
    batch = TaxFeeService(case.db).generate(
        case.manager, case.book["id"], period="2026-10",
        source_file_ids=[source["id"]], policy=POLICY,
    )
    assert batch["total_cents"] == 8000
    assert batch["rows"][0]["status"] == "adopted"
    assert batch["rows"][0]["adjustment_cents"] == -2000
    assert batch["rows"][0]["reason"] == "更正上期多计规费"


def test_manual_draft_edit_is_not_overwritten_by_same_batch_regeneration(business):
    from app.accounting.models import DraftLine, DraftUpdate

    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    edited = case.ledger.update_draft(case.manager, case.book["id"], draft["id"], DraftUpdate(
        expected_version=draft["version"],
        description="人工改稿保留",
        lines=[DraftLine(account_code=line["account_code"], debit=line["debit"], credit=line["credit"]) for line in draft["lines"]],
    ))
    again = service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    assert again["id"] == batch["id"]
    stored = case.ledger.get_draft(case.manager, case.book["id"], edited["id"])
    assert stored["description"] == "人工改稿保留"
    assert stored["version"] == edited["version"]


def test_concurrent_generation_returns_single_batch(business):
    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)

    def run():
        return service.generate(case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY)

    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = [future.result() for future in [pool.submit(run), pool.submit(run)]]
    assert len({batch["id"] for batch in batches}) == 1
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tax_fee_batches WHERE book_id=?", (case.book["id"],)).fetchone()[0] == 1
