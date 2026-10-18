"""B04 synthetic pilot: subledger, periodic drafts, report snapshot and close."""

from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import FirmMemberGrant
from app.b04_close.models import CloseCommand, ClosePreflight
from app.b04_close.service import CloseService
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.periodic.models import (
    AccrualRule,
    ClosingGenerate,
    DepreciableAsset,
    DraftCommand,
    DraftPost,
)
from app.periodic.service import PeriodicService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.subledger.models import (
    AllocationCreate,
    AllocationPart,
    AllocationReverse,
    BankLineCreate,
    BankMatch,
    OpenItemCreate,
)
from app.subledger.service import SubledgerService
from test_business import business
from test_posting_service import admin, frozen_opening


def setup_b03(case):
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"], package_version_id=opening["version_id"], reason="B04 绑定期初"
        ),
    )
    return opening, posting


def test_subledger_partial_payment_aging_control_and_bank(business):
    case = business
    opening, posting = setup_b03(case)
    subledger = SubledgerService(case.db)
    party = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="B04-C001", name="B04 虚构客户"),
    )
    payment = posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="b04-payment-001",
            book_id=case.book["id"],
            voucher_date="2026-10-20",
            description="支付应付账款",
            lines=[
                ManualLine(
                    account_id=opening["payable"]["id"],
                    account_version=1,
                    debit="3000.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="3000.00",
                ),
            ],
        ),
    )
    item = subledger.create_item(
        case.manager,
        OpenItemCreate(
            book_id=case.book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            original_date="2026-10-01",
            due_date="2026-10-15",
            amount="10000.00",
            source_kind="opening",
            reason="B04 期初未结应付款",
        ),
    )
    allocated = subledger.allocate(
        case.manager,
        AllocationCreate(
            book_id=case.book["id"],
            voucher_id=payment["voucher"]["id"],
            parts=[AllocationPart(item_id=item["id"], amount="3000.00")],
            idempotency_key="b04-alloc-001",
            reason="部分付款",
        ),
    )
    assert allocated["allocations"][0]["amount_cents"] == 300000
    listed = subledger.list_items(case.manager, case.book["id"])
    assert listed[0]["remaining_cents"] == 700000
    aging = subledger.aging(case.manager, case.book["id"], "2026-10-31")
    assert aging["buckets"]["1_30"] == 700000
    control = subledger.control_reconciliation(case.manager, case.book["id"])
    assert control["passed"] is True
    with pytest.raises(AccountingError, match="超过"):
        subledger.allocate(
            case.manager,
            AllocationCreate(
                book_id=case.book["id"],
                voucher_id=payment["voucher"]["id"],
                parts=[AllocationPart(item_id=item["id"], amount="8000.00")],
                idempotency_key="b04-alloc-over",
                reason="超额分配",
            ),
        )
    subledger.reverse_allocation(
        case.manager,
        allocated["allocations"][0]["id"],
        AllocationReverse(idempotency_key="b04-reverse-001", reason="撤销分配"),
    )
    assert subledger.list_items(case.manager, case.book["id"])[0]["remaining_cents"] == 1000000
    line = next(
        row
        for row in payment["lines"]
        if row["code_snapshot"] == "1002" and row["credit_cents"] == 300000
    )
    bank_line = subledger.add_bank_line(
        case.manager,
        BankLineCreate(
            book_id=case.book["id"],
            line_date="2026-10-20",
            description="银行付款",
            amount="-3000.00",
        ),
    )
    matched = subledger.match_bank_line(
        case.manager, bank_line["id"], BankMatch(voucher_line_id=line["id"])
    )
    assert matched["status"] == "matched"
    recon = subledger.bank_reconciliation(case.manager, case.book["id"])
    assert recon["unmatched_count"] == 0
    assert recon["passed"] is True


def test_report_is_stale_after_new_posting_and_reopen_is_blocked(business):
    case = business
    opening, posting = setup_b03(case)
    reports = ReportService(case.db)
    report = reports.create(case.manager, case.book["id"], "2026-10")
    report = reports.validate(case.manager, report["report"]["id"])
    report = reports.review(
        admin(case),
        report["report"]["id"],
        ReportCommand(expected_version=1, reason="复核初始报表"),
    )
    posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="b04-stale-posting",
            book_id=case.book["id"],
            voucher_date="2026-10-31",
            description="报表复核后新增业务",
            lines=[
                ManualLine(
                    account_id=opening["expense"]["id"],
                    account_version=1,
                    debit="1.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="1.00",
                ),
            ],
        ),
    )
    with pytest.raises(AccountingError, match="输入已变化"):
        reports.publish(
            case.manager,
            report["report"]["id"],
            ReportCommand(expected_version=1, reason="尝试发布旧报表"),
        )
    with pytest.raises(AccountingError, match="重开"):
        CloseService(case.db).reopen(
            case.manager, "not-a-run", CloseCommand(expected_version=1, reason="尝试重开月结")
        )


def test_periodic_accrual_asset_closing_reports_and_close(business):
    case = business
    opening, posting = setup_b03(case)
    periodic = PeriodicService(case.db)
    ledger_id = opening["ledger_id"]
    profit = OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=ledger_id,
            code="4103",
            name="本年利润",
            category="equity",
            normal_balance="credit",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="B04 本年利润",
        ),
    )
    accum = OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=ledger_id,
            code="1602",
            name="累计折旧",
            category="asset",
            normal_balance="credit",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="B04 累计折旧",
        ),
    )
    rule = periodic.create_accrual_rule(
        case.manager,
        AccrualRule(
            book_id=case.book["id"],
            name="月末计提",
            amount="100.00",
            debit_account_id=opening["expense"]["id"],
            debit_account_version=1,
            credit_account_id=opening["payable"]["id"],
            credit_account_version=1,
            reason="B04 合成计提",
        ),
    )
    accrual = periodic.generate_accrual(case.manager, rule["id"])
    periodic.approve(
        admin(case), accrual["draft"]["id"], DraftCommand(expected_version=1, reason="复核计提")
    )
    posted_accrual = periodic.post(
        case.manager,
        accrual["draft"]["id"],
        DraftPost(
            expected_version=2,
            command_key="b04-accrual-post",
            voucher_date="2026-10-31",
            reason="记账计提",
        ),
    )
    assert posted_accrual["draft"]["status"] == "posted"

    asset = periodic.create_asset(
        case.manager,
        DepreciableAsset(
            book_id=case.book["id"],
            kind="fixed",
            name="B04 虚构设备",
            cost="1200.00",
            residual="0.00",
            life_months=12,
            start_period="2026-10",
            expense_account_id=opening["expense"]["id"],
            expense_account_version=1,
            accum_account_id=accum["id"],
            accum_account_version=1,
            reason="B04 合成折旧",
        ),
    )
    depreciation = periodic.generate_asset(case.manager, asset["id"])
    periodic.approve(
        admin(case),
        depreciation["draft"]["id"],
        DraftCommand(expected_version=1, reason="复核折旧"),
    )
    periodic.post(
        case.manager,
        depreciation["draft"]["id"],
        DraftPost(
            expected_version=2,
            command_key="b04-dep-post",
            voucher_date="2026-10-31",
            reason="记账折旧",
        ),
    )
    # One fully matched bank movement gives the month-end bank evidence a
    # complete coverage line before closing; the opening bank balance is 10,000.
    bank_voucher = posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="b04-bank-evidence",
            book_id=case.book["id"],
            voucher_date="2026-10-31",
            description="虚构银行对账证据",
            lines=[
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="10000.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["payable"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="10000.00",
                ),
            ],
        ),
    )
    sub = SubledgerService(case.db)
    evidence_line = sub.add_bank_line(
        case.manager,
        BankLineCreate(
            book_id=case.book["id"],
            line_date="2026-10-31",
            description="虚构银行对账证据",
            amount="10000.00",
        ),
    )
    bank_line_id = next(
        line["id"] for line in bank_voucher["lines"] if line["account_id"] == opening["bank"]["id"]
    )
    sub.match_bank_line(
        case.manager,
        evidence_line["id"],
        BankMatch(voucher_line_id=bank_line_id),
    )
    closing = periodic.generate_closing(
        case.manager,
        ClosingGenerate(
            book_id=case.book["id"],
            period="2026-10",
            idempotency_key="b04-closing-generate",
            reason="B04 期末结转",
            profit_account_id=profit["id"],
            profit_account_version=1,
        ),
    )
    periodic.approve(
        admin(case), closing["draft"]["id"], DraftCommand(expected_version=1, reason="复核结转")
    )
    periodic.post(
        case.manager,
        closing["draft"]["id"],
        DraftPost(
            expected_version=2,
            command_key="b04-closing-post",
            voucher_date="2026-10-31",
            reason="记账结转",
        ),
    )
    trial = posting.trial_balance(case.manager, case.book["id"])
    assert trial["totals"]["closing_cents"] == 0

    reports = ReportService(case.db)
    report = reports.create(case.manager, case.book["id"], "2026-10")
    assert report["content"]["checks"]["balance_sheet_passed"] is True
    assert report["content"]["checks"]["cash_flow_passed"] is True
    assert report["content"]["checks"]["unclassified_cash_cents"] == 0
    report = reports.validate(case.manager, report["report"]["id"])
    report = reports.review(
        admin(case),
        report["report"]["id"],
        ReportCommand(expected_version=1, reason="复核报表"),
    )
    assert report["report"]["status"] == "reviewed"

    # The accrual payable now requires an explicit subledger open item;
    # otherwise the reverse reconciliation correctly blocks the close.
    party = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="B04-ACCRUAL", name="虚构计提供应商"),
    )
    SubledgerService(case.db).create_item(
        case.manager,
        OpenItemCreate(
            book_id=case.book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount="20100.00",
            source_kind="manual",
            reason="计提应付款、期初应付款与银行证据子账",
        ),
    )
    close = CloseService(case.db)
    run = close.preflight(
        case.manager,
        ClosePreflight(
            book_id=case.book["id"],
            period="2026-10",
            report_id=report["report"]["id"],
            idempotency_key="b04-close-preflight",
            reason="B04 月结预检",
        ),
    )
    assert run["close"]["status"] == "preflight_passed", [
        check for check in run["checks"] if check["status"] == "blocked"
    ]
    run = close.review(
        admin(case), run["close"]["id"], CloseCommand(expected_version=1, reason="复核月结")
    )
    assert run["close"]["status"] == "reviewed"
    run = close.lock(
        admin(case), run["close"]["id"], CloseCommand(expected_version=1, reason="内部锁期")
    )
    assert run["close"]["status"] == "locked"
    run = close.publish(
        admin(case), run["close"]["id"], CloseCommand(expected_version=1, reason="发布月结")
    )
    assert run["close"]["status"] == "published"
    with pytest.raises(AccountingError, match="锁期"):
        posting.post_manual(
            case.manager,
            ManualVoucher(
                command_key="b04-after-lock",
                book_id=case.book["id"],
                voucher_date="2026-10-31",
                description="锁期后尝试记账",
                lines=[
                    ManualLine(
                        account_id=opening["expense"]["id"],
                        account_version=1,
                        debit="1.00",
                        credit="0.00",
                    ),
                    ManualLine(
                        account_id=opening["bank"]["id"],
                        account_version=1,
                        debit="0.00",
                        credit="1.00",
                    ),
                ],
            ),
        )
