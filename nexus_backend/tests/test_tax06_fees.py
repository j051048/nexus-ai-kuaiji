from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.review_policy_models import ReviewPolicyChange
from app.ledgers.models import EntityCreate, TransportAssignmentCreate, TransportRelationCreate, TransportSubjectCreate, TransportSubjectUpdate
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


def _bank(case, direction, amount_cents, external_id, date="2026-10-10", counterparty="张师傅核算主体"):
    bank_id, draft_id = uuid4().hex, uuid4().hex
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,'bank')",
            (bank_id, case.book["id"], external_id, "银行", date, "规费到账/退款", counterparty,
             amount_cents, direction, f"bank:{external_id}", uuid4().hex),
        )
        conn.execute(
            "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation) VALUES(?,?,?,?,'draft',1,?)",
            (draft_id, case.book["id"], bank_id, "规费到账/退款", "测试"),
        )
        lines = (
            (("1002", amount_cents, 0), ("1122", 0, amount_cents))
            if direction == "income"
            else (("1122", amount_cents, 0), ("1002", 0, amount_cents))
        )
        for ordinal, (code, debit, credit) in enumerate(lines, 1):
            conn.execute(
                "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) VALUES(?,?,?,?,?,?)",
                (draft_id, case.book["id"], ordinal, code, debit, credit),
            )
    return bank_id


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
    receivables = service.list_receivables(case.actors["accountant"], case.book["id"])
    assert receivables["row_count"] == 3
    assert receivables["total_cents"] == reviewed["total_cents"] == 22500
    assert len({item["driver_id"] for item in receivables["items"]}) == 2
    assert all(item["driver_name"] != "未匹配驾驶员" for item in receivables["items"])
    assert receivables["items"][0]["settlement_status"] == "unsettled"
    with case.db.transaction(actor=case.manager) as conn:
        stored = conn.execute("SELECT batch_row_id,amount_cents,status FROM fee_receivable_items WHERE batch_id=? ORDER BY batch_row_id", (batch["id"],)).fetchall()
    assert len(stored) == 3
    assert len({row["batch_row_id"] for row in stored}) == 3
    assert sum(int(row["amount_cents"]) for row in stored) == 22500
    assert {row["status"] for row in stored} == {"open"}
    with pytest.raises(AccountingError):
        service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="重复")



def test_fee_receivable_partial_and_full_bank_allocations(business):
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
    service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="独立复核")
    receivable = service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]
    bank_id, draft_id = uuid4().hex, uuid4().hex
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,'bank')",
            (bank_id, case.book["id"], "BANK-FEE-1", "银行", "2026-10-10", "规费到账", "张师傅核算主体",
             int(receivable["amount_cents"]), "income", "bank:test", uuid4().hex),
        )
        conn.execute(
            "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation) VALUES(?,?,?,?,'draft',1,?)",
            (draft_id, case.book["id"], bank_id, "规费到账", "测试"),
        )
        for ordinal, (code, debit, credit) in enumerate((("1002", int(receivable["amount_cents"]), 0), ("1122", 0, int(receivable["amount_cents"]))), 1):
            conn.execute(
                "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) VALUES(?,?,?,?,?,?)",
                (draft_id, case.book["id"], ordinal, code, debit, credit),
            )
    partial = service.allocate_receivable(
        case.actors["accountant"], case.book["id"], receivable["receivable_id"],
        bank_source_id=bank_id, amount_cents=4000, idempotency_key="alloc-1",
    )
    assert partial["amount_cents"] == 4000
    remaining = service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]
    assert remaining["settlement_status"] == "unsettled"
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], receivable["receivable_id"],
        bank_source_id=bank_id, amount_cents=5000, idempotency_key="alloc-2",
    )
    settled = service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]
    assert settled["settlement_status"] == "settled"
    with pytest.raises(AccountingError):
        service.allocate_receivable(
            case.actors["accountant"], case.book["id"], receivable["receivable_id"],
            bank_source_id=bank_id, amount_cents=1, idempotency_key="alloc-3",
        )
    allocations = service.list_allocations(case.actors["accountant"], case.book["id"])["items"]
    assert len(allocations) == 2
    reversed_row = service.reverse_allocation(
        case.actors["accountant"], case.book["id"], allocations[0]["allocation_id"], "银行退回",
    )
    assert reversed_row["receivable_status"] == "open"
    reopened = service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]
    assert reopened["settlement_status"] == "unsettled"
    bank = service.list_bank_sources(case.actors["accountant"], case.book["id"])["items"][0]
    assert bank["remaining_cents"] == 5000
    with pytest.raises(AccountingError):
        service.reverse_allocation(
            case.actors["accountant"], case.book["id"], allocations[0]["allocation_id"], "重复",
        )

def test_fee_allocations_span_banks_and_one_bank_spans_receivables(business):
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
    service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="独立复核")
    items = sorted(service.list_receivables(case.actors["accountant"], case.book["id"])["items"], key=lambda row: row["amount_cents"])
    small, middle, large = items[0], items[1], items[-1]
    bank_a = _bank(case, "income", 15000, "BANK-MULTI-A")
    bank_b = _bank(case, "income", 7500, "BANK-MULTI-B")
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], small["receivable_id"],
        bank_source_id=bank_a, amount_cents=int(small["amount_cents"]), idempotency_key="multi-a-small", collection_basis="同一银行合并到账，按驾驶员分配表拆分",
    )
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], middle["receivable_id"],
        bank_source_id=bank_a, amount_cents=int(middle["amount_cents"]), idempotency_key="multi-a-middle", collection_basis="同一银行合并到账，按驾驶员分配表拆分",
    )
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], large["receivable_id"],
        bank_source_id=bank_a, amount_cents=1500, idempotency_key="multi-a-large", collection_basis="同一银行合并到账，按驾驶员分配表拆分",
    )
    later_bank = _bank(case, "income", 4000, "BANK-MULTI-LATE", date="2026-09-30")
    with pytest.raises(AccountingError) as cross_period:
        service.allocate_receivable(
            case.actors["accountant"], case.book["id"], large["receivable_id"],
            bank_source_id=later_bank, amount_cents=1000, idempotency_key="multi-late",
        )
    assert cross_period.value.status == 409
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], large["receivable_id"],
        bank_source_id=bank_b, amount_cents=int(large["amount_cents"]) - 1500,
        idempotency_key="multi-b-large", collection_basis="同一银行合并到账，按驾驶员分配表拆分",
    )
    settled = {row["receivable_id"]: row["settlement_status"] for row in service.list_receivables(case.actors["accountant"], case.book["id"])["items"]}
    assert settled[small["receivable_id"]] == "settled"
    assert settled[large["receivable_id"]] == "settled"
    banks = {row["source_id"]: row["remaining_cents"] for row in service.list_bank_sources(case.actors["accountant"], case.book["id"])["items"]}
    assert banks.get(bank_a, 0) == 0
    assert banks.get(bank_b, 0) == 0
    first = next(row for row in service.list_allocations(case.actors["accountant"], case.book["id"])["items"] if row["receivable_id"] == small["receivable_id"] and row["bank_source_id"] == bank_a)
    service.reverse_allocation(case.actors["accountant"], case.book["id"], first["allocation_id"], "银行退回")
    reopened = service.list_bank_sources(case.actors["accountant"], case.book["id"])["items"]
    assert {row["source_id"]: row["remaining_cents"] for row in reopened}[bank_a] == int(small["amount_cents"])
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], small["receivable_id"],
        bank_source_id=bank_a, amount_cents=int(small["amount_cents"]), idempotency_key="multi-a-reuse", collection_basis="同一银行合并到账，按驾驶员分配表拆分",
    )


def test_fee_refund_uses_expense_bank_and_reopens_receivable(business):
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
    service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="独立复核")
    receivable = service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]
    amount = int(receivable["amount_cents"])
    income_bank = _bank(case, "income", amount, "BANK-REFUND-IN")
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], receivable["receivable_id"],
        bank_source_id=income_bank, amount_cents=amount, idempotency_key="refund-source-alloc",
    )
    expense_bank = _bank(case, "expense", amount, "BANK-REFUND-OUT")

    refund = service.refund_receivable(
        case.actors["accountant"], case.book["id"], receivable["receivable_id"],
        bank_source_id=expense_bank, amount_cents=3000, reason="多收退回驾驶员",
        idempotency_key="refund-1",
    )
    assert refund["amount_cents"] == 3000
    assert refund["receivable_status"] == "open"
    assert refund["driver_name"] == receivable["driver_name"]
    assert service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]["settlement_status"] == "unsettled"
    assert service.list_refunds(case.actors["accountant"], case.book["id"])["items"][0]["reason"] == "多收退回驾驶员"

    same = service.refund_receivable(
        case.actors["accountant"], case.book["id"], receivable["receivable_id"],
        bank_source_id=expense_bank, amount_cents=3000, reason="多收退回驾驶员",
        idempotency_key="refund-1",
    )
    assert same["refund_id"] == refund["refund_id"]
    with pytest.raises(AccountingError) as changed:
        service.refund_receivable(
            case.actors["accountant"], case.book["id"], receivable["receivable_id"],
            bank_source_id=expense_bank, amount_cents=3001, reason="多收退回驾驶员",
            idempotency_key="refund-1",
        )
    assert changed.value.status == 409
    with pytest.raises(AccountingError) as wrong_direction:
        service.refund_receivable(
            case.actors["accountant"], case.book["id"], receivable["receivable_id"],
            bank_source_id=income_bank, amount_cents=1000, reason="方向错误",
            idempotency_key="refund-income",
        )
    assert wrong_direction.value.status == 409
    with pytest.raises(AccountingError) as too_much:
        service.refund_receivable(
            case.actors["accountant"], case.book["id"], receivable["receivable_id"],
            bank_source_id=expense_bank, amount_cents=amount - 2999, reason="超过净额",
            idempotency_key="refund-too-much",
        )
    assert too_much.value.status == 409
    assert service.list_bank_sources(
        case.actors["accountant"], case.book["id"], direction="expense",
    )["items"][0]["remaining_cents"] == amount - 3000

    second_bank = _bank(case, "income", 3000, "BANK-REFUND-RECOLLECT")
    service.allocate_receivable(
        case.actors["accountant"], case.book["id"], receivable["receivable_id"],
        bank_source_id=second_bank, amount_cents=3000, idempotency_key="refund-realloc",
    )
    assert service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]["settlement_status"] == "settled"
    original = next(row for row in service.list_allocations(case.actors["accountant"], case.book["id"])["items"] if row["amount_cents"] == amount)
    reversed_after_refund = service.reverse_allocation(
        case.actors["accountant"], case.book["id"], original["allocation_id"], "退款后撤销原核销",
    )
    assert reversed_after_refund["receivable_status"] == "open"
    assert service.list_receivables(case.actors["accountant"], case.book["id"])["items"][0]["settlement_status"] == "unsettled"


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


def test_fee_auxiliaries_use_canonical_names_not_identity_ids(business):
    import json
    case = business
    _masters(case)
    source = _source(case)
    batch = TaxFeeService(case.db).generate(
        case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY,
    )
    with case.db.connect() as conn:
        rows = conn.execute(
            "SELECT values_json FROM draft_line_auxiliaries WHERE draft_id=? ORDER BY ordinal",
            (batch["draft_id"],),
        ).fetchall()
    values = [json.loads(row["values_json"]) for row in rows]
    assert values == [
        {"counterparty": "张师傅核算主体"}, {"project": "京A1核算主体"},
        {"counterparty": "李师傅核算主体"}, {"project": "京A2核算主体"},
    ]
    assert all("transport_subjects" not in json.dumps(value) for value in values)


def test_fee_guard_rejects_legacy_id_only_auxiliary_snapshot(business):
    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(
        case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY,
    )
    with case.db.transaction(actor=case.manager) as conn:
        conn.execute(
            "UPDATE draft_line_auxiliaries SET values_json=? WHERE draft_id=? AND ordinal=1",
            ('{"counterparty":"legacy-driver-id"}', batch["draft_id"]),
        )
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert any("辅助核算" in issue for issue in draft["issues"])
    with pytest.raises(AccountingError):
        service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="独立复核")


def test_fee_guard_rejects_stale_transport_mapping_after_subject_deactivation(business):
    case = business
    _masters(case)
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(
        case.manager, case.book["id"], period="2026-10", source_file_ids=[source["id"]], policy=POLICY,
    )
    with case.db.transaction(actor=case.manager) as conn:
        driver = conn.execute("SELECT id FROM transport_subjects WHERE external_id='D1'").fetchone()
    LedgersService(case.db).update_transport_subject(
        case.manager, case.book["id"], driver["id"],
        TransportSubjectUpdate(expected_version=1, display_name="张师傅", active=False, reason="车辆停用"),
    )
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert any("已变化" in issue for issue in draft["issues"])
    with pytest.raises(AccountingError):
        service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="独立复核")


def test_fee_batch_is_bound_to_review_policy_version(business):
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
    batch = service.generate(
        case.manager, case.book["id"], period="2026-10",
        source_file_ids=[source["id"]], policy=POLICY,
    )
    with case.db.transaction(actor=case.manager) as conn:
        version = ReviewPolicyService.policy_in_transaction(conn, case.book["id"])["version"]
    ReviewPolicyService(case.db).configure(
        case.manager, case.book["id"],
        ReviewPolicyChange(expected_version=version, mode="independent", reason="测试政策变更"),
    )
    with pytest.raises(AccountingError) as stale:
        service.review(
            case.actors["accountant"], case.book["id"], batch["id"],
            expected_version=1, reason="政策变更后复核",
        )
    assert stale.value.status == 409
    assert "政策" in str(stale.value)

    fresh = service.generate(
        case.manager, case.book["id"], period="2026-10",
        source_file_ids=[source["id"]], policy=POLICY,
    )
    reviewed = service.review(
        case.actors["accountant"], case.book["id"], fresh["id"],
        expected_version=1, reason="按新政策独立复核",
    )
    assert reviewed["status"] == "reviewed"



def test_fee_allocation_requires_basis_for_a_different_payer(business):
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
    service.review(case.actors["accountant"], case.book["id"], batch["id"], expected_version=1, reason="代收测试复核")
    item = service.list_receivables(case.manager, case.book["id"])["items"][0]
    other = _bank(case, "income", 5000, "BANK-PAYER-OTHER", counterparty="王先生")
    with pytest.raises(AccountingError) as missing:
        service.allocate_receivable(
            case.manager, case.book["id"], item["receivable_id"],
            bank_source_id=other, amount_cents=1000, idempotency_key="payer-no-basis",
        )
    assert missing.value.status == 409
    allocation = service.allocate_receivable(
        case.manager, case.book["id"], item["receivable_id"],
        bank_source_id=other, amount_cents=1000, idempotency_key="payer-with-basis",
        payer_name="王先生", collection_basis="王先生代张师傅付款，附微信说明与身份核对",
    )
    assert allocation["payer_name"] == "王先生"
    assert "微信说明" in allocation["collection_basis"]
    same = _bank(case, "income", 5000, "BANK-PAYER-SAME", counterparty="张师傅核算主体")
    allocation = service.allocate_receivable(
        case.manager, case.book["id"], item["receivable_id"],
        bank_source_id=same, amount_cents=1000, idempotency_key="payer-same-no-basis",
    )
    assert allocation["payer_name"] == "张师傅核算主体"
    assert allocation["collection_basis"] is None
