"""Re-use the accounting kernel; synthetic inputs, no external verification."""

import json

import pytest

from app.accounting.domain import AccountingError
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.periodic.models import DepreciableAsset, DraftCommand, DraftPost
from app.periodic.service import PeriodicService
from app.subledger.service import SubledgerService
from test_b04_pilot import setup_b03
from test_business import business  # noqa: F401
from test_posting_service import admin


def asset_case(
    case, *, cost="100.00", residual="0.00", life=3, start="2026-08", account_end="2026-12"
):
    opening, _ = setup_b03(case)
    account = OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=opening["ledger_id"],
            code="1602",
            name="累计折旧",
            category="asset",
            normal_balance="credit",
            effective_from="2026-01",
            effective_to=account_end,
            reason="合成资产测试",
        ),
    )
    service = PeriodicService(case.db)
    asset = service.create_asset(
        case.manager,
        DepreciableAsset(
            book_id=case.book["id"],
            kind="fixed",
            name="尾差测试设备",
            cost=cost,
            residual=residual,
            life_months=life,
            start_period=start,
            expense_account_id=opening["expense"]["id"],
            expense_account_version=1,
            accum_account_id=account["id"],
            accum_account_version=1,
            reason="虚构资产录入依据",
        ),
    )
    return service, asset


def test_expired_asset_account_is_rejected_before_recording(business):
    with pytest.raises(AccountingError) as error:
        asset_case(business, account_end="2026-09")
    assert error.value.status == 409
    with business.db.transaction(actor=business.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM periodic_assets WHERE book_id=?", (business.book["id"],)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "cost,residual,life,start,expected",
    [
        ("100.00", "0.00", 3, "2026-08", 10000),
        ("100.00", "1.00", 7, "2026-04", 9900),
        ("0.01", "0.00", 12, "2025-11", 1),
        ("100.00", "0.00", 3, "2026-10", 3333),
    ],
)
def test_depreciation_tail_and_first_month(business, cost, residual, life, start, expected):
    service, asset = asset_case(business, cost=cost, residual=residual, life=life, start=start)
    result = service.generate_asset(business.manager, asset["id"])
    assert sum(line["debit_cents"] for line in result["lines"]) == expected
    assert sum(line["credit_cents"] for line in result["lines"]) == expected
    assert result["draft"]["status"] == "draft"
    assert (
        service.generate_asset(business.manager, asset["id"])["draft"]["id"]
        == result["draft"]["id"]
    )


def test_asset_creation_retains_reason_in_audit(business):
    _, asset = asset_case(business)
    with business.db.transaction(actor=business.manager) as conn:
        row = conn.execute(
            "SELECT details FROM audit_events WHERE object_id=? AND action='periodic.asset.create'",
            (asset["id"],),
        ).fetchone()
    assert row is not None
    assert json.loads(row["details"])["reason"] == "虚构资产录入依据"


@pytest.mark.parametrize("as_of", ["2026-02-30", "2026-13-01", "20261031", "2026-W44-6"])
def test_aging_rejects_invalid_or_noncanonical_date(business, as_of):
    setup_b03(business)
    with pytest.raises(AccountingError) as error:
        SubledgerService(business.db).aging(business.manager, business.book["id"], as_of)
    assert error.value.status == 422


def test_aging_scope_and_separate_kind_totals(business):
    setup_b03(business)
    value = SubledgerService(business.db).aging(business.manager, business.book["id"], "2026-10-31")
    assert value["scope"] == {
        "firm_id": business.manager.firm_id,
        "company_id": business.company["id"],
        "book_id": business.book["id"],
        "period": "2026-10",
    }
    assert set(value["by_kind"]) == {"receivable", "payable", "advance_paid", "advance_received"}
    assert all(sum(buckets.values()) == 0 for buckets in value["by_kind"].values())


def test_periodic_workspace_review_then_post_and_scope(business):
    service, asset = asset_case(business)
    generated = service.generate_asset(business.manager, asset["id"])
    view = service.workspace(business.manager, business.book["id"])
    assert view["scope"]["company_id"] == business.company["id"]
    assert view["assets"][0]["id"] == asset["id"]
    assert view["drafts"][0]["lines"] == generated["lines"]
    draft = generated["draft"]
    command = DraftCommand(expected_version=draft["version"], reason="对照资产核对")
    with pytest.raises(AccountingError):
        service.approve(business.manager, draft["id"], command)
    with pytest.raises(AccountingError):
        service.post(
            business.manager,
            draft["id"],
            DraftPost(
                expected_version=1,
                command_key="too-early",
                voucher_date="2026-10-31",
                reason="不允许越过复核",
            ),
        )
    reviewed = service.approve(admin(business), draft["id"], command)
    posted = service.post(
        business.manager,
        draft["id"],
        DraftPost(
            expected_version=reviewed["draft"]["version"],
            command_key="tail-final",
            voucher_date="2026-10-31",
            reason="复核后记账",
        ),
    )
    assert posted["draft"]["status"] == "posted"
    assert (
        service.workspace(business.manager, business.book["id"])["assets"][0]["posted_cents"]
        == 10000
    )
    with pytest.raises(AccountingError):
        service.workspace(business.actors["employee"], business.book["id"])
    with pytest.raises(AccountingError):
        service.workspace(business.actors["accountant"], business.other_book["id"])
