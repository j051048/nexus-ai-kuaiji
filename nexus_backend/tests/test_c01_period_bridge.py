"""Real frozen opening -> reviewed close -> next-period residual bridge."""

from review_policy_fixtures import post_reviewed_manual

from types import SimpleNamespace
from uuid import uuid4
import pytest
import sqlite3
from app.accounting.domain import AccountingError
from app.ledgers.models import EntityCreate, PeriodCreate
from app.ledgers.service import LedgersService
from app.posting.models import ManualVoucher, ManualLine, OpeningBind
from app.posting.service import PostingService
from app.subledger.models import (
    ControlAccountCreate,
    OpenItemCreate,
    AllocationCreate,
    AllocationPart,
    BankLineCreate,
    BankMatch,
)
from app.opening.models import OpeningPackageCreate, OpeningImport, OpeningLineInput, OpeningCommand
from app.opening.service import OpeningService
from app.reports.service import ReportService
from app.reports.models import ReportCommand
from app.b04_close.models import ClosePreflight, CloseCommand
from app.b04_close.service import CloseService
from test_business import business
from test_c01_bank_evidence import coverage_case, declaration, review
from test_posting_service import admin


def test_first_period_requires_frozen_start_and_reports_legacy_identity(coverage_case):
    c = coverage_case
    result = c.sub.continuity(c.c.manager, c.c.book["id"])
    assert result["status"] == "frozen_start"
    assert result["passed"] and result["continuity_not_verified"]


def test_bank_binding_needs_another_current_reviewer(coverage_case):
    from app.subledger.models import BankIdentityCreate, BankBindingCreate, BankBindingReview

    x = coverage_case
    c = x.c
    stable = x.sub.create_bank_identity(
        c.manager,
        BankIdentityCreate(book_id=c.book["id"], name="长期真实账户", reason="明确长期银行身份"),
    )
    req = BankBindingCreate(
        book_id=c.book["id"],
        bank_account_id=x.account["id"],
        stable_bank_id=stable["id"],
        reason="本期账户归属确认",
        idempotency_key="binding-test-1",
    )
    binding = x.sub.bind_bank_identity(c.manager, req)
    with pytest.raises(AccountingError):
        x.sub.review_bank_binding(
            c.manager, binding["id"], BankBindingReview(decision="approved", reason="不能自提自审")
        )
    done = x.sub.review_bank_binding(
        c.actors["accountant"],
        binding["id"],
        BankBindingReview(decision="approved", reason="独立账户归属复核"),
    )
    assert done["decision"] == "approved"
    assert x.sub.bind_bank_identity(c.manager, req)["id"] == binding["id"]
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE bank_accounts SET aux_json=? WHERE id=?",
                ('{"project":"unreviewed-other-bucket"}', x.account["id"]),
            )


def test_previous_period_cannot_disappear_from_continuity(coverage_case):
    c = coverage_case.c
    target = LedgersService(c.db).create_period(
        c.manager,
        c.book["id"],
        PeriodCreate(
            period="2026-11",
            name="次月",
            source_book_id=c.book["id"],
            idempotency_key="next-for-missing-bridge",
        ),
    )
    result = coverage_case.sub.continuity(c.manager, target["book_id"])
    assert not result["passed"] and result["status"] == "bridge_required"


def prepare_bridge(x, delta=0):
    from app.subledger.models import (
        BankIdentityCreate,
        BankBindingCreate,
        BankBindingReview,
        BankAccountCreate,
        PeriodBridgeCreate,
    )

    c = x.c
    sub = x.sub
    reviewer = admin(c)
    with c.db.transaction(actor=c.manager) as conn:
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
        accounts = {
            r["code"]: dict(r)
            for r in conn.execute(
                "SELECT a.id,v.code FROM ledger_accounts a JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=1 WHERE a.ledger_id=?",
                (ledger,),
            )
        }
        policy = conn.execute(
            "SELECT id FROM accounting_policies WHERE ledger_id=?", (ledger,)
        ).fetchone()[0]
    sub.configure_control_account(
        c.manager,
        ControlAccountCreate(
            book_id=c.book["id"],
            kind="payable",
            control_account_id=accounts["2202"]["id"],
            control_account_version=1,
            effective_from="2026-01",
            reason="桥接应付真实勾稽",
        ),
    )
    party = LedgersService(c.db).create_entity(
        c.manager,
        c.book["id"],
        EntityCreate(kind="counterparty", code="BRIDGE", name="真实测试供应商"),
    )
    item = sub.create_item(
        c.manager,
        OpenItemCreate(
            book_id=c.book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=accounts["2202"]["id"],
            control_account_version=1,
            original_date="2026-10-01",
            due_date="2026-12-01",
            amount="10000.00",
            reason="桥接期初应付事项",
        ),
    )
    voucher = post_reviewed_manual(
        PostingService(c.db),
        c.manager,
        ManualVoucher(
            book_id=c.book["id"],
            voucher_date="2026-10-01",
            command_key="bridge-payment",
            description="真实合成付款三角钱",
            lines=[
                ManualLine(account_id=accounts["2202"]["id"], account_version=1, debit="0.30"),
                ManualLine(account_id=accounts["1002"]["id"], account_version=1, credit="0.30"),
            ],
        ),
    )
    sub.allocate(
        c.manager,
        AllocationCreate(
            book_id=c.book["id"],
            voucher_id=voucher["voucher"]["id"],
            parts=[AllocationPart(item_id=item["id"], amount="0.30")],
            idempotency_key="bridge-pay-allocation",
            reason="逐笔核销真实付款",
        ),
    )
    line = sub.add_bank_line(
        c.manager,
        BankLineCreate(
            book_id=c.book["id"],
            bank_account_id=x.account["id"],
            line_date="2026-10-01",
            description="合成原件三角钱付款",
            amount="-0.30",
        ),
    )
    sub.match_bank_line(
        c.manager,
        line["id"],
        BankMatch(
            voucher_line_id=next(
                r["id"] for r in voucher["lines"] if r["account_id"] == accounts["1002"]["id"]
            )
        ),
    )
    review(x, reviewer, sub.declare_bank_coverage(c.manager, declaration(x)))
    stable = sub.create_bank_identity(
        c.manager,
        BankIdentityCreate(book_id=c.book["id"], name="长期结算账户", reason="本地银行身份确认"),
    )

    def bind(book_id, bank_id):
        claim = sub.bind_bank_identity(
            c.manager,
            BankBindingCreate(
                book_id=book_id,
                bank_account_id=bank_id,
                stable_bank_id=stable["id"],
                idempotency_key="bind-" + book_id,
                reason="真实本月银行归属",
            ),
        )
        sub.review_bank_binding(
            reviewer, claim["id"], BankBindingReview(decision="approved", reason="独立银行账户复核")
        )

    bind(c.book["id"], x.account["id"])
    reports = ReportService(c.db)
    report = reports.create(c.manager, c.book["id"], "2026-10")
    rid = report["report"]["id"]
    reports.validate(c.manager, rid)
    reports.review(reviewer, rid, ReportCommand(expected_version=1, reason="独立复核本地报表"))
    close = CloseService(c.db)
    run = close.preflight(
        c.manager,
        ClosePreflight(
            book_id=c.book["id"],
            period="2026-10",
            report_id=rid,
            idempotency_key="bridge-real-close",
            reason="真实月结独立复核",
        ),
    )
    assert run["close"]["status"] == "preflight_passed", run["checks"]
    command = CloseCommand(expected_version=1, reason="独立复核并锁定前期")
    close.review(reviewer, run["close"]["id"], command)
    close.lock(reviewer, run["close"]["id"], command)
    target = LedgersService(c.db).create_period(
        c.manager,
        c.book["id"],
        PeriodCreate(
            period="2026-11",
            name="桥接次月",
            source_book_id=c.book["id"],
            idempotency_key="bridge-real-next",
        ),
    )["book_id"]
    opening = OpeningService(c.db)
    package = opening.create_package(
        c.manager, OpeningPackageCreate(ledger_id=ledger, target_period="2026-11", policy_id=policy)
    )
    from decimal import Decimal

    amount = str(Decimal(999970 + delta) / 100)
    version = opening.import_version(
        c.manager,
        package["package"]["id"],
        OpeningImport(
            idempotency_key="bridge-next-opening",
            reason="复核次月真实期初",
            lines=[
                OpeningLineInput(
                    account_id=accounts["1002"]["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit=amount,
                    ytd_credit="0.00",
                    opening=amount,
                ),
                OpeningLineInput(
                    account_id=accounts["2202"]["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="0.00",
                    ytd_credit=amount,
                    opening="-" + amount,
                ),
            ],
        ),
    )["version"]["id"]
    opening.validate_version(c.manager, version)
    cmd = OpeningCommand(expected_version=1, reason="独立复核次月冻结期初")
    opening.submit_version(c.manager, version, cmd)
    opening.approve_version(reviewer, version, cmd)
    opening.freeze_version(reviewer, version, cmd)
    PostingService(c.db).bind_opening(
        c.manager,
        OpeningBind(book_id=target, package_version_id=version, reason="绑定次月已核准期初"),
    )
    account = sub.create_bank_account(
        c.manager,
        BankAccountCreate(
            book_id=target,
            name="同一长期账户次月绑定",
            ledger_account_id=accounts["1002"]["id"],
            ledger_account_version=1,
            reason="次月明确账户归属",
        ),
    )
    bind(target, account["id"])
    req = PeriodBridgeCreate(
        book_id=target,
        source_book_id=c.book["id"],
        idempotency_key="bridge-real-import",
        reason="由前期锁期真实导入未结余额",
    )
    return SimpleNamespace(sub=sub, c=c, target=target, item=item, request=req)


def test_true_locked_period_bridge_imports_residual_and_replays_once(coverage_case):
    x = prepare_bridge(coverage_case)
    assert not x.sub.control_reconciliation(x.c.manager, x.target)["passed"]
    first = x.sub.create_period_bridge(x.c.manager, x.request)
    assert first["status"] == "complete" and len(first["items"]) == 1
    assert first["items"][0]["remaining_cents"] == 999970
    assert x.sub.create_period_bridge(x.c.manager, x.request) == first
    items = x.sub.list_items(x.c.manager, x.target)
    assert len(items) == 1 and items[0]["original_date"] == "2026-10-01"
    assert x.sub.control_reconciliation(x.c.manager, x.target)["passed"]
    with x.c.db.transaction(actor=x.c.manager) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM vouchers WHERE book_id=?", (x.target,)).fetchone()[0]
            == 0
        )
    with pytest.raises(AccountingError):
        x.sub.create_period_bridge(
            x.c.manager, x.request.model_copy(update={"reason": "同键不同内容必须拒绝"})
        )


def test_frozen_but_wrong_next_opening_cannot_be_bridged(coverage_case):
    x = prepare_bridge(coverage_case, delta=1)
    with pytest.raises(AccountingError):
        x.sub.create_period_bridge(x.c.manager, x.request)
    assert x.sub.list_items(x.c.manager, x.target) == []
