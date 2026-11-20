"""Independent A–D acceptance: use real services, never a mocked success."""

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.service import AccountingService
from app.integrations import tplus54
from app.tax.fees import TaxFeeService
from app.tax.sources import TaxSourceAdapter
from test_business import business  # noqa: F401
from test_tax06_fees import COLS, POLICY, ROWS, _masters, _source
from test_tax04_sources import xlsx


def prepared(case, *, rows=None):
    _masters(case)
    source = _source(case, rows=ROWS[:3] if rows is None else rows)
    fees = TaxFeeService(case.db)
    batch = fees.generate(case.manager, case.book["id"], period="2026-10",
                          source_file_ids=[source["id"]], policy=POLICY)
    return fees, source, batch


@pytest.mark.parametrize("operation", ["get", "validate", "invalidate"])
def test_employee_cannot_read_or_invalidate_fee_batch(business, operation):
    fees, _, batch = prepared(business)
    with pytest.raises(AccountingError) as failure:
        args = (business.actors["employee"], business.book["id"], batch["id"])
        if operation == "invalidate":
            fees.invalidate(*args, expected_version=1, reason="无权使他人草稿失效")
        else:
            getattr(fees, operation)(*args)
    assert failure.value.status in (403, 404)
    assert fees.get(business.manager, business.book["id"], batch["id"])["status"] == "draft"


@pytest.mark.parametrize("change", ["version", "row_review", "invalidate"])
def test_generic_export_rechecks_fee_source_not_only_special_panel(business, change):
    fees, source, batch = prepared(business)
    fees.review(business.actors["accountant"], business.book["id"], batch["id"],
                expected_version=1, reason="独立复核真实来源")
    if change == "version":
        _source(business, rows=ROWS[:3], version=2)
    elif change == "row_review":
        TaxSourceAdapter(business.db).review_row(
            business.manager, business.book["id"], source["id"], 2,
            status="excluded", reason="原件核对发现不应采用")
    else:
        fees.invalidate(business.manager, business.book["id"], batch["id"],
                        expected_version=2, reason="核定失效")
    with pytest.raises(AccountingError):
        business.ledger.export(business.actors["accountant"], business.book["id"],
                              ExportRequest(draft_ids=[batch["draft_id"]], idempotency_key="stale-fee"))
    with business.db.transaction(actor=business.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM exports WHERE book_id=?",
                            (business.book["id"],)).fetchone()[0] == 0


def test_generic_review_cannot_bypass_unresolved_fee_rows(business):
    _, _, batch = prepared(business, rows=ROWS)
    with pytest.raises(AccountingError):
        business.ledger.approve(business.actors["accountant"], business.book["id"],
                                batch["draft_id"], ApprovalRequest(expected_version=1))


def test_source_row_review_changes_semantic_fingerprint(business):
    _, source, _ = prepared(business)
    updated = TaxSourceAdapter(business.db).review_row(
        business.manager, business.book["id"], source["id"], 2,
        status="excluded", reason="不参与本次核定")
    assert updated["fingerprint"] != source["fingerprint"]
    assert updated["sha256"] == source["sha256"]  # Original bytes never change.


def test_bank_external_id_cannot_disable_direction_validation():
    source = {"external_id": "tax-fee:user-controlled", "book_id": "b",
              "source_kind": "bank", "amount_cents": 10000, "direction": "income"}
    lines = [{"account_code": "1122", "debit_cents": 10000, "credit_cents": 0},
             {"account_code": "6001", "debit_cents": 0, "credit_cents": 10000}]
    issues = AccountingService._calculate_issues(source, lines, {"1122", "6001"}, "真实原件")
    assert any("银行" in issue for issue in issues)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
def test_source_nonfinite_amount_is_a_row_issue_not_server_error(business, amount):
    rows = [ROWS[0], [*ROWS[1]]]
    rows[1][3] = amount
    source = _source(business, rows=rows)
    assert source["rows"][0]["status"] == "needs_review"
    assert source["amount_total_cents"] == 0


def test_missing_date_cannot_be_assumed_to_be_current_period(business):
    rows = [ROWS[0], [*ROWS[1]]]
    rows[1][1] = ""
    source = _source(business, rows=rows)
    assert source["rows"][0]["status"] == "needs_review"


def test_source_mapping_change_requires_new_version(business):
    source = _source(business, rows=ROWS[:3])
    with pytest.raises(AccountingError):
        TaxSourceAdapter(business.db).import_source(
            business.manager, business.book["id"], filename="规费.xlsx",
            data=xlsx({"运输明细": ROWS[:3]}), source_system="客户运输台账", external_id="S-FEE",
            version=1, sheet_name="运输明细", column_map={**COLS, "amount": "数量"},
            rule_version="tax-source-v1")
    assert source["amount_total_cents"] == 18000


def test_receipt_duplicate_voucher_is_not_full_coverage():
    content = "行号,外部凭证编号,结果,原因\n1,T54-1,accepted,\n2,T54-1,rejected,冲突\n".encode()
    with pytest.raises(AccountingError):
        tplus54.parse_result(content, ["T54-1"])


def test_zero_only_source_remains_part_of_fee_snapshot(business):
    fees, source, _ = prepared(business)
    zero = TaxSourceAdapter(business.db).import_source(
        business.manager, business.book["id"], filename="zero.xlsx",
        data=xlsx({"运输明细": [ROWS[0], ROWS[3]]}), source_system="客户运输台账",
        external_id="S-ZERO", version=1, sheet_name="运输明细", column_map=COLS,
        rule_version="tax-source-v1")
    batch = fees.generate(business.manager, business.book["id"], period="2026-10",
                          source_file_ids=[source["id"], zero["id"]], policy=POLICY)
    assert batch["stale"] is False
    assert {item["id"] for item in batch["source_files"]} == {source["id"], zero["id"]}
    assert fees.validate(business.manager, business.book["id"], batch["id"])["stale"] is False
    assert fees.review(business.actors["accountant"], business.book["id"], batch["id"],
                       expected_version=1, reason="核对包含零额来源的完整集合")["status"] == "reviewed"


def test_source_original_is_downloadable_and_hash_verified(business):
    import hashlib
    adapter = TaxSourceAdapter(business.db)
    source = _source(business, rows=ROWS[:3])
    name, content = adapter.download_original(business.manager, business.book["id"], source["id"])
    assert name == "规费.xlsx"
    assert hashlib.sha256(content).hexdigest() == source["sha256"]
    with pytest.raises(AccountingError):
        adapter.download_original(business.actors["employee"], business.book["id"], source["id"])
    with business.db.transaction(write=True, actor=business.manager) as conn:
        conn.execute("UPDATE tax_source_files SET original_content_base64=? WHERE id=?", ("eA==", source["id"]))
    with pytest.raises(AccountingError):
        adapter.download_original(business.manager, business.book["id"], source["id"])


def test_fee_policy_cannot_relabel_an_expense_as_revenue(business):
    _masters(business)
    source = _source(business, rows=ROWS[:3])
    with pytest.raises(AccountingError):
        TaxFeeService(business.db).generate(business.manager, business.book["id"],
            period="2026-10", source_file_ids=[source["id"]],
            policy={**POLICY, "vehicle_revenue_account": "6602.02"})


def test_frozen_adapter_cannot_present_stale_fee_as_current(business):
    from app.integrations.service import FinancialBridgeService
    from app.integrations.models import BridgeConfiguration, BridgeExport, BridgeReceiptImport
    from test_tax08_tplus54 import receipt
    fees, _, batch = prepared(business)
    fees.review(business.actors["accountant"], business.book["id"], batch["id"],
                expected_version=1, reason="独立核对规费")
    exported = business.ledger.export(business.manager, business.book["id"],
        ExportRequest(draft_ids=[batch["draft_id"]], idempotency_key="taxi-stale-bridge"))
    bridge = FinancialBridgeService(business.db)
    draft = business.ledger.get_draft(business.manager, business.book["id"], batch["draft_id"])
    account_codes = sorted({line["account_code"] for line in draft["lines"]})
    auxiliary_pairs = sorted({
        (kind, value)
        for line in draft["lines"]
        for kind, value in line.get("auxiliary_values", {}).items()
    })
    bridge.configure(business.manager, business.book["id"], BridgeConfiguration(expected_version=0,
        external_book_id="synthetic-only",
        accounts=[{"local_code": code, "external_code": code} for code in account_codes],
        auxiliaries=[{"kind": kind, "local_value": value, "external_code": value}
                     for kind, value in auxiliary_pairs],
        reason="离线映射核定", idempotency_key="taxi-config"))
    intent = bridge.prepare_export(business.manager, business.book["id"], BridgeExport(
        export_id=exported["id"], configuration_version=1, reason="冻结独立复核集合", idempotency_key="taxi-intent"))
    artifact = bridge.create_artifact(business.manager, business.book["id"], intent["id"])
    _source(business, rows=ROWS[:3], version=2)
    with pytest.raises(AccountingError):
        bridge.download_artifact(business.manager, business.book["id"], artifact["id"])
    with pytest.raises(AccountingError):
        bridge.record_receipt(business.manager, business.book["id"], artifact["id"], BridgeReceiptImport(
            receipt_code="stale-receipt", content=receipt(tplus54.build_rows(intent["package"])[1]), reason="旧版不可冒充当前结果"))
