from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.service import AccountingService
from app.tax.leases import (
    LeaseAmend,
    LeaseConfirmation,
    LeaseCreate,
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
    view = _recognize(case, contract["id"], "2026-10")
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
