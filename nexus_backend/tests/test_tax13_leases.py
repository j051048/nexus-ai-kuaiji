from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.service import AccountingService
from app.tax.leases import (
    LeaseAmend,
    LeaseApprove,
    LeaseConfirmation,
    LeaseCreate,
    LeaseInvoiceRed,
    LeasePolicy,
    LeaseRecognition,
    LeaseRefund,
    LeaseService,
)
from test_business import business  # noqa: F401

POLICY = LeasePolicy(version="lease-v1", revenue_account="6001")


def _advance_account(case):
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts(book_id,code,name,active) VALUES(?,?,?,1)",
            (case.book["id"], "2203", "预收账款"),
        )


def _create(case, start="2026-10", end="2026-12", monthly="1000.00"):
    return LeaseService(case.db).create(
        case.manager, case.book["id"],
        LeaseCreate(
            customer_name="虚构客运客户", customer_external_id="CUS-LEASE-1",
            vehicle_external_id="V-LEASE-1", start_period=start, end_period=end, monthly=monthly,
        ),
    )


def _confirm(case, contract_id, kind, period, amount, reference):
    return LeaseService(case.db).confirm(
        case.manager, case.book["id"], contract_id,
        LeaseConfirmation(kind=kind, period=period, amount=amount, reference=reference),
    )


def _recognize(case, contract_id, period):
    return LeaseService(case.db).recognize(
        case.manager, case.book["id"], contract_id,
        LeaseRecognition(period=period, policy=POLICY, reason="按合同与业务确认确认收入"),
    )


def test_lease_plan_recognizes_only_with_business_confirmation(business):
    case = business
    _advance_account(case)
    contract = _create(case)
    assert len(contract["plan"]) == 3
    with pytest.raises(AccountingError) as exc:
        _recognize(case, contract["id"], "2026-10")
    assert exc.value.status == 409 and "业务确认" in exc.value.detail

    _confirm(case, contract["id"], "receipt", "2026-10", "1200.00", "BANK-1")
    _confirm(case, contract["id"], "business", "2026-10", "1000.00", "SERVICE-1")
    _confirm(case, contract["id"], "invoice", "2026-10", "1000.00", "INV-LEASE-1")
    view = _recognize(case, contract["id"], "2026-10")
    assert {row["kind"] for row in view["confirmations"]} == {"business", "receipt", "invoice"}
    assert [row["reference"] for row in view["confirmations"] if row["kind"] == "invoice"] == ["INV-LEASE-1"]
    october = [row for row in view["plan"] if row["period"] == "2026-10" and row["kind"] == "revenue"][0]
    assert october["status"] == "recognized"
    assert october["advance_cents"] == 100000
    draft = AccountingService(case.db).get_draft(case.manager, case.book["id"], october["draft_id"])
    assert draft["issues"] == []
    assert [(line["account_code"], line["debit"], line["credit"]) for line in draft["lines"]] == [
        ("2203", "1000.00", "0.00"),
        ("6001", "0.00", "1000.00"),
    ]

    _confirm(case, contract["id"], "business", "2026-11", "1000.00", "SERVICE-2")
    november = _recognize(case, contract["id"], "2026-11")
    row = [item for item in november["plan"] if item["period"] == "2026-11" and item["kind"] == "revenue"][0]
    assert row["advance_cents"] == 20000
    draft = AccountingService(case.db).get_draft(case.manager, case.book["id"], row["draft_id"])
    assert [(line["account_code"], line["debit"], line["credit"]) for line in draft["lines"]] == [
        ("2203", "200.00", "0.00"),
        ("1122", "800.00", "0.00"),
        ("6001", "0.00", "1000.00"),
    ]
    repeated = _recognize(case, contract["id"], "2026-10")
    assert len([item for item in repeated["plan"] if item["kind"] == "revenue" and item["status"] == "recognized"]) == 2


def test_lease_confirmations_refunds_amend_and_permissions(business):
    case = business
    _advance_account(case)
    contract = _create(case)
    _confirm(case, contract["id"], "receipt", "2026-10", "1200.00", "BANK-1")
    _confirm(case, contract["id"], "business", "2026-10", "1000.00", "SERVICE-1")
    with pytest.raises(AccountingError):
        _confirm(case, contract["id"], "business", "2026-10", "999.00", "SERVICE-BAD")
    with pytest.raises(AccountingError) as exc:
        _confirm(case, contract["id"], "receipt", "2026-10", "1.00", "BANK-1")
    assert exc.value.status == 409

    refund = LeaseService(case.db).refund(
        case.manager, case.book["id"], contract["id"],
        LeaseRefund(period="2026-10", amount="300.00", reference="REFUND-1", policy=POLICY, reason="客户提前退车，按合同退款"),
    )
    assert refund["refunded_cents"] == 30000
    with pytest.raises(AccountingError):
        LeaseService(case.db).refund(
            case.manager, case.book["id"], contract["id"],
            LeaseRefund(period="2026-10", amount="100.00", reference="REFUND-2", policy=POLICY, reason="重复退款应拒绝"),
        )

    amended = LeaseService(case.db).amend(
        case.manager, case.book["id"], contract["id"],
        LeaseAmend(expected_version=1, end_period="2027-01", monthly="1100.00", reason="合同续签并调整月租"),
    )
    assert amended["version"] == 2
    assert len(amended["plan"]) == 4
    with pytest.raises(AccountingError) as exc:
        LeaseService(case.db).amend(
            case.manager, case.book["id"], contract["id"],
            LeaseAmend(expected_version=1, end_period="2027-01", monthly="1200.00", reason="旧版本应拒绝"),
        )
    assert exc.value.status == 409

    with pytest.raises(AccountingError):
        LeaseService(case.db).get(case.actors["accountant"], case.book["id"], "not-found")
    terminated = LeaseService(case.db).terminate(case.manager, case.book["id"], contract["id"], "客户终止合同")
    assert terminated["status"] == "terminated"
    assert all(row["status"] != "planned" for row in terminated["plan"])


def test_amended_lease_requires_explicit_current_version_approval(business):
    case = business
    _advance_account(case)
    contract = _create(case)
    amended = LeaseService(case.db).amend(
        case.manager, case.book["id"], contract["id"],
        LeaseAmend(expected_version=1, end_period="2027-01", monthly="1100.00", reason="合同续签并调整月租"),
    )
    assert amended["version"] == 2
    assert amended["approved_version"] == 1
    assert amended["stale_approval"] is True
    _confirm(case, contract["id"], "receipt", "2026-11", "1100.00", "BANK-2")
    _confirm(case, contract["id"], "business", "2026-11", "1100.00", "SERVICE-2")
    with pytest.raises(AccountingError) as exc:
        _recognize(case, contract["id"], "2026-11")
    assert exc.value.status == 409 and "改版" in exc.value.detail
    approved = LeaseService(case.db).approve(
        case.manager, case.book["id"], contract["id"], 2,
    )
    assert approved["approved_version"] == 2
    assert approved["stale_approval"] is False
    view = _recognize(case, contract["id"], "2026-11")
    row = [item for item in view["plan"] if item["period"] == "2026-11" and item["kind"] == "revenue"][0]
    assert row["status"] == "recognized"


def test_export_of_old_lease_version_is_rejected_after_amendment(business):
    case = business
    _advance_account(case)
    contract = _create(case)
    _confirm(case, contract["id"], "receipt", "2026-10", "1000.00", "BANK-1")
    _confirm(case, contract["id"], "business", "2026-10", "1000.00", "SERVICE-1")
    view = _recognize(case, contract["id"], "2026-10")
    draft = [row for row in view["plan"] if row["period"] == "2026-10" and row["kind"] == "revenue"][0]["draft_id"]
    draft_view = AccountingService(case.db).approve(
        case.actors["accountant"], case.book["id"], draft, ApprovalRequest(expected_version=1)
    )
    assert draft_view["status"] == "approved"
    LeaseService(case.db).amend(
        case.manager, case.book["id"], contract["id"],
        LeaseAmend(expected_version=1, end_period="2027-01", monthly="1100.00", reason="合同续签"),
    )
    with pytest.raises(AccountingError) as exc:
        AccountingService(case.db).export(
            case.actors["accountant"], case.book["id"],
            ExportRequest(draft_ids=[draft], idempotency_key="lease-stale-export-1"),
        )
    assert exc.value.status == 409 and "改版" in exc.value.detail


def test_invoice_red_reversal_keeps_history_and_rejects_over_red(business):
    case = business
    _advance_account(case)
    contract = _create(case)
    _confirm(case, contract["id"], "receipt", "2026-10", "1000.00", "BANK-1")
    _confirm(case, contract["id"], "business", "2026-10", "1000.00", "SERVICE-1")
    _confirm(case, contract["id"], "invoice", "2026-10", "1000.00", "INV-1")
    service = LeaseService(case.db)
    view = service.red_invoice(
        case.manager, case.book["id"], contract["id"],
        LeaseInvoiceRed(
            original_reference="INV-1", red_reference="RED-1", period="2026-10",
            amount="1000.00", reason="客户退租，原发票全额红冲",
        ),
    )
    assert len(view["invoice_reds"]) == 1
    assert view["invoice_reds"][0]["original_reference"] == "INV-1"
    assert len(view["confirmations"]) == 3  # 红冲不删除原发票事实
    with pytest.raises(AccountingError) as exc:
        service.red_invoice(
            case.manager, case.book["id"], contract["id"],
            LeaseInvoiceRed(
                original_reference="INV-1", red_reference="RED-2", period="2026-10",
                amount="1000.00", reason="重复红冲",
            ),
        )
    assert exc.value.status == 409 and "已红冲" in exc.value.detail
