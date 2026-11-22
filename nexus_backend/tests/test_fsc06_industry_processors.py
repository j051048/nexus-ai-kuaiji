"""Batch 06 三家行业处理器：正例主链、高频阻断、幂等与患者隐私红线。

隐私用例是**强负向**断言：不仅要求 422，还要求零落盘（信封/事件/审计）与零日志泄漏，
并且拒绝文案本身不得回显患者姓名或身份证号。
"""

import hashlib
import json
import logging

import pytest

from app.accounting.domain import Actor, AccountingError
from app.fsc.contracts import ContractWrite, FscContractService
from app.fsc.imports import FscImportService, ImportEnvelopeWrite
from app.fsc.industry import (
    HospitalSummaryDrafts,
    IndustryDraftWrite,
    PropertyServiceDrafts,
    TradeInventoryDrafts,
)
from app.fsc.profiles import FscProfileService, ProfileConfirm, ProfileWrite, REQUIRED
from test_business import business  # noqa: F401

PATIENT_NAME = "张三丰"
PATIENT_ID = "11010119900101123X"


def _confirmed_profile(case, company_id):
    confirmations = {
        key: {
            "status": "confirmed",
            "owner": "财务负责人",
            "evidence_ref": f"客户确认/{key}",
            "confirmed_at": "2026-11-01T09:00:00+08:00",
        }
        for key in REQUIRED
    }
    payload = {key: None for key in REQUIRED}
    payload["_confirmations"] = confirmations
    service = FscProfileService(case.db)
    draft = service.create_draft(
        case.manager, company_id, ProfileWrite(industry="多行业", payload=payload)
    )
    return service.confirm(
        case.manager, draft["id"], ProfileConfirm(expected_version=draft["version"])
    )


def _second_manager(case):
    user_id = "fsc06-second-manager"
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(id,name,active) VALUES(?,?,1)", (user_id, "第二管理员")
        )
        conn.execute(
            "INSERT OR REPLACE INTO memberships(firm_id,user_id,role) VALUES(?,?,'admin')",
            (case.manager.firm_id, user_id),
        )
    return Actor(user_id, case.manager.firm_id)


def _active_contract(case, company_id, processor_code, capabilities, policy):
    _confirmed_profile(case, company_id)
    service = FscContractService(case.db)
    draft = service.create_draft(
        case.manager,
        company_id,
        ContractWrite(
            industry_code="多行业",
            processor_code=processor_code,
            capabilities=capabilities,
            source_contracts=[
                {
                    "code": "main",
                    "object": processor_code,
                    "fields": ["external_id", "date", "amount"],
                }
            ],
            evidence_policy=policy,
        ),
    )
    service.confirm(_second_manager(case), draft["id"], draft["version"])
    return service.activate(case.manager, draft["id"], draft["version"])


def _envelope(
    rows, *, source_system="property", source_object="service_ledger", tag="p1", version=1
):
    return ImportEnvelopeWrite(
        source_system=source_system,
        source_object=source_object,
        external_id=f"EXT-{tag}",
        source_version=version,
        filename="行业台账.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256=hashlib.sha256(f"{tag}-{version}".encode()).hexdigest(),
        size_bytes=2048,
        mapping_version="map-v1",
        rule_version="rule-v1",
        unit="CNY",
        currency="CNY",
        rows=rows,
    )


def _confirmed_envelope(case, rows, **kwargs):
    service = FscImportService(case.db)
    envelope = service.import_envelope(case.manager, case.book["id"], _envelope(rows, **kwargs))
    return service.confirm(case.manager, envelope["id"], envelope["source_version"])


def _row(
    external_id, amount, *, quantity=0, target_kind="service_contract", target_id="SC1", raw=None
):
    return {
        "external_id": external_id,
        "date": "2026-10-02",
        "amount_cents": amount,
        "quantity_milli": quantity,
        "unit": "CNY",
        "currency": "CNY",
        "target_kind": target_kind,
        "target_id": target_id,
        "raw": raw or {},
    }


# ---------------------------------------------------------------- 物业与食材


def _property_contract(case):
    return _active_contract(
        case,
        case.company["id"],
        "property_service",
        ["property_service_billing"],
        {"source_original": "required"},
    )


def test_property_amortization_and_grocery_movements_are_deterministic(business):
    case = business
    _property_contract(case)
    envelope = _confirmed_envelope(
        case,
        [
            _row("P1", 30_000, raw={"amortize_months": 3}),
            _row("P2", 20_000, target_id="SC2", raw={"amortize_months": 1}),
            _row(
                "G1",
                20_000,
                quantity=2_000,
                target_kind="warehouse",
                target_id="W1",
                raw={"grocery_kind": "grocery_receipt", "unit_price_cents": 10_000},
            ),
            _row(
                "G2",
                5_000,
                quantity=500,
                target_kind="warehouse",
                target_id="W1",
                raw={"grocery_kind": "grocery_return", "unit_price_cents": 10_000},
            ),
            _row(
                "G3",
                1_500,
                quantity=0,
                target_kind="warehouse",
                target_id="W1",
                raw={"grocery_kind": "grocery_price_variance", "unit_price_cents": 0},
            ),
        ],
    )
    service = PropertyServiceDrafts(case.db)
    amortization = service.generate(
        case.manager,
        case.book["id"],
        IndustryDraftWrite(envelope_id=envelope["id"]),
        kind="property_amortization",
    )
    assert amortization["kind"] == "property_amortization"
    assert amortization["total_cents"] == 50_000
    assert amortization["lines"] == [
        {"contract_id": "SC1", "amount_cents": 30_000, "months": 3, "row_numbers": [1]},
        {"contract_id": "SC2", "amount_cents": 20_000, "months": 1, "row_numbers": [2]},
    ]
    groceries = service.generate(
        case.manager,
        case.book["id"],
        IndustryDraftWrite(envelope_id=envelope["id"]),
        kind="grocery_movement",
    )
    assert [(item["action"], item["amount_cents"]) for item in groceries["lines"]] == [
        ("grocery_price_variance", 1_500),
        ("grocery_receipt", 20_000),
        ("grocery_return", -5_000),
    ]
    assert groceries["total_cents"] == 16_500
    # 同一信封 + 同一契约 + 同一产物 = 同一草稿，重复生成只重放。
    again = service.generate(
        case.manager,
        case.book["id"],
        IndustryDraftWrite(envelope_id=envelope["id"]),
        kind="grocery_movement",
    )
    assert again["replayed"] is True and again["id"] == groceries["id"]
    assert again["fingerprint"] == groceries["fingerprint"]


def test_property_refuses_illegal_unit_price_and_out_of_period_rows(business):
    case = business
    _property_contract(case)
    # 单价在 raw 里，信封层不管它；处理器必须拒绝而不是按金额倒推。
    envelope = _confirmed_envelope(
        case,
        [
            _row(
                "G1",
                20_000,
                quantity=2_000,
                target_kind="warehouse",
                target_id="W1",
                raw={"grocery_kind": "grocery_receipt", "unit_price_cents": -1},
            )
        ],
        tag="p1b",
    )
    with pytest.raises(AccountingError) as refused:
        PropertyServiceDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=envelope["id"]),
            kind="grocery_movement",
        )
    assert refused.value.status == 422 and "单价" in refused.value.detail
    # 数量与金额对不上时同样拒绝（不允许用金额倒推单价）。
    mismatch = _confirmed_envelope(
        case,
        [
            _row(
                "G2",
                20_000,
                quantity=2_000,
                target_kind="warehouse",
                target_id="W1",
                raw={"grocery_kind": "grocery_receipt", "unit_price_cents": 9_000},
            )
        ],
        tag="p1c",
    )
    with pytest.raises(AccountingError) as inconsistent:
        PropertyServiceDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=mismatch["id"]),
            kind="grocery_movement",
        )
    assert "不一致" in inconsistent.value.detail
    # 跨期行在信封入口就被拒（不可能进入处理器）。
    with pytest.raises(AccountingError) as out_of_period:
        _confirmed_envelope(
            case,
            [
                _row(
                    "G9",
                    100,
                    quantity=1,
                    target_kind="warehouse",
                    raw={"grocery_kind": "grocery_receipt", "unit_price_cents": 100},
                )
                | {"date": "2026-11-02"}
            ],
            tag="p2",
        )
    assert out_of_period.value.status == 422


def test_property_requires_a_confirmed_envelope_and_active_contract(business):
    case = business
    service = FscImportService(case.db)
    draft = service.import_envelope(
        case.manager,
        case.book["id"],
        _envelope([_row("P1", 30_000, raw={"amortize_months": 1})], tag="p3"),
    )
    with pytest.raises(AccountingError) as unconfirmed:
        PropertyServiceDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=draft["id"]),
            kind="property_amortization",
        )
    assert unconfirmed.value.status == 409 and "尚未确认" in unconfirmed.value.detail
    service.confirm(case.manager, draft["id"], draft["source_version"])
    with pytest.raises(AccountingError) as no_contract:
        PropertyServiceDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=draft["id"]),
            kind="property_amortization",
        )
    assert no_contract.value.status == 409 and "契约" in no_contract.value.detail


# ---------------------------------------------------------------- 贸易存货

TRADE_ROWS = [
    _row("C1", 100_000, quantity=2_000, raw={"document": "contract", "match_key": "K1"}),
    _row("I1", 60_000, quantity=1_000, raw={"document": "inbound", "match_key": "K1"}),
    _row("I2", 40_000, quantity=1_000, raw={"document": "inbound", "match_key": "K1"}),
    _row("V1", 100_000, quantity=2_000, raw={"document": "invoice", "match_key": "K1"}),
    _row("O1", 0, quantity=1_500, raw={"document": "outbound", "match_key": "K1"}),
]


def test_trade_three_way_match_and_costing_method_are_approved_only(business):
    case = business
    _active_contract(case, case.company["id"], "trade_inventory", ["trade_inventory_match"], {})
    envelope = _confirmed_envelope(
        case, TRADE_ROWS, source_system="trade", source_object="inventory", tag="t1"
    )
    with pytest.raises(AccountingError) as no_method:
        TradeInventoryDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=envelope["id"]),
            kind="trade_cost_carry",
        )
    assert no_method.value.status == 409 and "计价方法" in no_method.value.detail
    assert TradeInventoryDrafts(case.db).list(case.manager, case.book["id"])["items"] == [], (
        "未批准计价方法时不能留下任何草稿"
    )


def test_trade_cost_carry_follows_the_approved_method(business):
    case = business
    service = TradeInventoryDrafts(case.db)
    for method, expected in (("moving_weighted_average", 75_000), ("fifo", 80_000)):
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(
                "UPDATE fsc_industry_contracts SET status='inactive' WHERE company_id=?",
                (case.company["id"],),
            )
        _active_contract(
            case,
            case.company["id"],
            "trade_inventory",
            ["trade_inventory_match"],
            {"inventory_costing_method": method},
        )
        envelope = _confirmed_envelope(
            case, TRADE_ROWS, source_system="trade", source_object="inventory", tag=f"t-{method}"
        )
        matched = service.generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=envelope["id"]),
            kind="trade_purchase_match",
        )
        assert matched["total_cents"] == 100_000
        carry = service.generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=envelope["id"]),
            kind="trade_cost_carry",
        )
        assert carry["lines"][0]["method"] == method
        assert carry["lines"][0]["quantity_milli"] == 1_500
        assert carry["total_cents"] == expected, (method, carry["lines"])


def test_trade_refuses_missing_or_inconsistent_documents(business):
    case = business
    _active_contract(
        case,
        case.company["id"],
        "trade_inventory",
        ["trade_inventory_match"],
        {"inventory_costing_method": "fifo"},
    )
    missing_invoice = [row for row in TRADE_ROWS if row["raw"]["document"] != "invoice"]
    envelope = _confirmed_envelope(
        case, missing_invoice, source_system="trade", source_object="inventory", tag="t2"
    )
    with pytest.raises(AccountingError) as incomplete:
        TradeInventoryDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=envelope["id"]),
            kind="trade_cost_carry",
        )
    assert incomplete.value.status == 409 and "三单核对不全" in incomplete.value.detail
    mismatch = [dict(row) for row in TRADE_ROWS]
    mismatch[3] = _row("V1", 99_999, quantity=2_000, raw={"document": "invoice", "match_key": "K1"})
    other = _confirmed_envelope(
        case, mismatch, source_system="trade", source_object="inventory", tag="t3"
    )
    with pytest.raises(AccountingError) as inconsistent:
        TradeInventoryDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=other["id"]),
            kind="trade_cost_carry",
        )
    assert inconsistent.value.status == 409 and "不一致" in inconsistent.value.detail
    over_out = [dict(row) for row in TRADE_ROWS]
    over_out[4] = _row("O1", 0, quantity=2_500, raw={"document": "outbound", "match_key": "K1"})
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "UPDATE fsc_industry_contracts SET status='inactive' WHERE company_id=?",
            (case.company["id"],),
        )
    _active_contract(
        case,
        case.company["id"],
        "trade_inventory",
        ["trade_inventory_match"],
        {"inventory_costing_method": "moving_weighted_average"},
    )
    over_env = _confirmed_envelope(
        case, over_out, source_system="trade", source_object="inventory", tag="t4"
    )
    with pytest.raises(AccountingError) as over_refused:
        TradeInventoryDrafts(case.db).generate(
            case.manager,
            case.book["id"],
            IndustryDraftWrite(envelope_id=over_env["id"]),
            kind="trade_cost_carry",
        )
    assert over_refused.value.status == 409 and "超过入库数量" in over_refused.value.detail


# ---------------------------------------------------------------- 内部医院


def _hospital_contract(case):
    return _active_contract(
        case,
        case.company["id"],
        "hospital_summary",
        ["hospital_department_summary"],
        {"source_original": "required"},
    )


def test_hospital_department_summary_only(business):
    case = business
    _hospital_contract(case)
    envelope = _confirmed_envelope(
        case,
        [
            _row(
                "H1",
                120_000,
                quantity=30,
                target_kind="department",
                target_id="D-内科",
                raw={"patient_count": 30},
            ),
            _row("H2", 80_000, quantity=20, target_kind="department", target_id="D-内科"),
            _row("H3", 60_000, quantity=15, target_kind="department", target_id="D-外科"),
        ],
        source_system="hospital_summary",
        source_object="department_summary",
        tag="h1",
    )
    draft = HospitalSummaryDrafts(case.db).generate(
        case.manager, case.book["id"], IndustryDraftWrite(envelope_id=envelope["id"])
    )
    assert draft["kind"] == "hospital_department_summary"
    assert draft["total_cents"] == 260_000
    assert draft["lines"] == [
        {
            "department_id": "D-内科",
            "amount_cents": 200_000,
            "quantity_milli": 50,
            "row_numbers": [1, 2],
        },
        {
            "department_id": "D-外科",
            "amount_cents": 60_000,
            "quantity_milli": 15,
            "row_numbers": [3],
        },
    ]
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO fsc_import_envelopes(id,firm_id,company_id,book_id,period,source_system,"
            "source_object,external_id,source_version,filename,media_type,sha256,size_bytes,"
            "mapping_version,rule_version,unit,currency,rows_json,row_count,adopted_count,total_cents,"
            "status,fingerprint,created_by,created_at) "
            "SELECT 'raw-hospital',firm_id,company_id,book_id,period,'hospital_summary',"
            "'department_summary','RAW-1',1,filename,media_type,sha256,size_bytes,mapping_version,"
            "rule_version,unit,currency,?,1,1,1000,'confirmed','ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',created_by,created_at "
            "FROM fsc_import_envelopes WHERE id=?",
            (
                json.dumps(
                    [
                        _row(
                            "H9",
                            1_000,
                            target_kind="department",
                            target_id="D-内科",
                            raw={"患者姓名": PATIENT_NAME},
                        )
                    ],
                    ensure_ascii=False,
                ),
                envelope["id"],
            ),
        )
    # 第二层：即使信封已绕过入口落库，处理器侧仍必须整包拒收。
    with pytest.raises(AccountingError) as refused:
        HospitalSummaryDrafts(case.db).generate(
            case.manager, case.book["id"], IndustryDraftWrite(envelope_id="raw-hospital")
        )
    assert refused.value.status == 422
    assert PATIENT_NAME not in refused.value.detail


def _counts(case):
    with case.db.transaction(actor=case.manager) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "fsc_import_envelopes",
                "fsc_import_envelope_events",
                "audit_events",
                "fsc_industry_drafts",
            )
        }


@pytest.mark.parametrize(
    "rows",
    [
        [_row("H1", 120_000, raw={"patient_name": PATIENT_NAME})],
        [_row("H1", 120_000, raw={"id_card": PATIENT_ID})],
        [_row("H1", 120_000, raw={"病历号": "MR-2026-0001"})],
        [_row("H1", 120_000, raw={"就诊卡号": "V-000123"})],
        [_row("H1", 120_000, raw={"summary": f"患者身份证号 {PATIENT_ID} 已核对"})],
        [_row("H1", 120_000, raw={"note": "医保明细 123456789012345"})],
        [_row("H1", 120_000, raw={"id_num": "110101800101001"})],
        [_row("H1", 120_000, raw={"patient_name_total": PATIENT_NAME})],
        [_row("H1", 120_000, raw={"tags": [f"核对 {PATIENT_ID}"]})],
    ],
)
def test_hospital_privacy_guard_rejects_whole_package_without_any_trace(business, caplog, rows):
    case = business
    _hospital_contract(case)
    before = _counts(case)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AccountingError) as refused:
        FscImportService(case.db).import_envelope(
            case.manager,
            case.book["id"],
            _envelope(
                rows,
                source_system="hospital_summary",
                source_object="department_summary",
                tag="privacy",
            ),
        )
    assert refused.value.status == 422
    assert "整包已拒收" in refused.value.detail
    # 拒绝文案不得回显患者真实取值。
    assert PATIENT_NAME not in refused.value.detail
    assert PATIENT_ID not in refused.value.detail
    # 0 落盘：信封、事件、审计、草稿都没有新增。
    assert _counts(case) == before
    # 0 日志泄漏。
    assert PATIENT_NAME not in caplog.text
    assert PATIENT_ID not in caplog.text
    assert "MR-2026-0001" not in caplog.text


def test_hospital_privacy_guard_also_blocks_identifiers_hidden_in_other_sources(business):
    case = business
    before = _counts(case)
    with pytest.raises(AccountingError) as refused:
        FscImportService(case.db).import_envelope(
            case.manager,
            case.book["id"],
            _envelope(
                [_row("X1", 100, raw={"note": f"身份证号 {PATIENT_ID}"})],
                source_system="property",
                source_object="service_ledger",
                tag="disguised",
            ),
        )
    assert refused.value.status == 422
    assert _counts(case) == before
