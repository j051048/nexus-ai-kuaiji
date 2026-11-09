"""Existing financial services must be operable without weakening review boundaries."""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.subledger.models import AllocationCreate, AllocationPart, AllocationReverse, OpenItemCreate
from app.subledger.service import SubledgerService
from test_b04_pilot import setup_b03, configure_payable
from test_business import business  # noqa: F401
from test_c01_closure import payment


def settlement_case(c):
    opening, _ = setup_b03(c)
    configure_payable(c, opening)
    sub = SubledgerService(c.db)
    party = LedgersService(c.db).create_entity(
        c.manager, c.book["id"], EntityCreate(kind="counterparty", code="CONT", name="虚构往来方")
    )
    item = sub.create_item(
        c.manager,
        OpenItemCreate(
            book_id=c.book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount="10000.00",
            reason="虚构期初往来",
        ),
    )
    voucher = payment(c, opening["payable"]["id"], opening["bank"]["id"])
    return sub, item, voucher


def allocate(c, sub, item, voucher, fingerprint):
    return sub.allocate(
        c.manager,
        AllocationCreate(
            book_id=c.book["id"],
            voucher_id=voucher["voucher"]["id"],
            parts=[AllocationPart(item_id=item["id"], amount="3000.00")],
            expected_fingerprint=fingerprint,
            idempotency_key=uuid4().hex,
            reason="已核对付款凭证",
        ),
    )


def test_settlement_detail_partial_reverse_and_stale_version(business):
    c = business
    sub, item, voucher = settlement_case(c)
    view = sub.item_workspace(c.manager, c.book["id"], item["id"])
    assert view["scope"]["company_id"] == c.company["id"]
    assert view["item"]["remaining_cents"] == 1000000
    assert view["can_allocate"] is True
    assert view["vouchers"][0]["id"] == voucher["voucher"]["id"]
    assert view["vouchers"][0]["available_cents"] == 300000
    assert view["source"]["verified"] is False  # a manual declaration is not an original
    result = allocate(c, sub, item, voucher, view["fingerprint"])
    after = sub.item_workspace(c.manager, c.book["id"], item["id"])
    assert after["item"]["remaining_cents"] == 700000
    assert after["vouchers"] == []
    assert after["allocations"][0]["reason"] == "已核对付款凭证"
    with pytest.raises(AccountingError) as stale:
        allocate(c, sub, item, voucher, view["fingerprint"])
    assert stale.value.status == 409
    sub.reverse_allocation(
        c.manager,
        result["allocations"][0]["id"],
        AllocationReverse(
            idempotency_key=uuid4().hex,
            reason="人工核对需撤回核销",
            expected_fingerprint=after["fingerprint"],
        ),
    )
    final = sub.item_workspace(c.manager, c.book["id"], item["id"])
    assert final["item"]["remaining_cents"] == 1000000
    assert final["allocations"][0]["reversal_reason"] == "人工核对需撤回核销"
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM vouchers WHERE book_id=?", (c.book["id"],)
            ).fetchone()[0]
            == 1
        )


def test_settlement_detail_never_crosses_scope_or_grants_employee_access(business):
    c = business
    sub, item, _ = settlement_case(c)
    with pytest.raises(AccountingError):
        sub.item_workspace(c.manager, c.other_book["id"], item["id"])
    with pytest.raises(AccountingError):
        sub.item_workspace(c.actors["employee"], c.book["id"], item["id"])


def test_settlement_preview_fingerprint_changes_on_other_allocation(business):
    c = business
    sub, item, voucher = settlement_case(c)
    view = sub.item_workspace(c.manager, c.book["id"], item["id"])
    allocate(c, sub, item, voucher, view["fingerprint"])
    assert (
        sub.item_workspace(c.manager, c.book["id"], item["id"])["fingerprint"]
        != view["fingerprint"]
    )


def next_period(c, period):
    from app.ledgers.models import PeriodCreate

    return LedgersService(c.db).create_period(
        c.manager,
        c.book["id"],
        PeriodCreate(
            period=period,
            name="连续期间" + period,
            source_book_id=c.book["id"],
            idempotency_key=uuid4().hex,
        ),
    )["book_id"]


def test_asset_keeps_identity_across_year_and_blocks_unfinished_prior(business):
    from test_finance_agent_reuse import asset_case

    c = business
    service, asset = asset_case(c, start="2026-12", life=3, account_end="2027-12")
    december = next_period(c, "2026-12")
    january = next_period(c, "2027-01")
    view = service.workspace(c.manager, december)
    assert [a["id"] for a in view["assets"]] == [asset["id"]]
    assert view["assets"][0]["origin_book_id"] == c.book["id"]
    draft = service.generate_asset(c.manager, asset["id"], book_id=december)
    assert draft["draft"]["book_id"] == december
    assert draft["lines"][0]["debit_cents"] == 3333
    assert service.generate_asset(c.manager, asset["id"], book_id=december)["replayed"]
    with pytest.raises(AccountingError) as prior:
        service.generate_asset(c.manager, asset["id"], book_id=january)
    assert prior.value.status == 409
    assert "前期" in str(prior.value)
    with pytest.raises(AccountingError):
        service.generate_asset(c.manager, asset["id"], book_id=c.other_book["id"])
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM periodic_assets WHERE id=?", (asset["id"],)
            ).fetchone()[0]
            == 1
        )


def test_asset_account_change_invalidates_review(business):
    from test_finance_agent_reuse import asset_case
    from test_posting_service import admin
    from app.periodic.models import DraftCommand
    from app.opening.models import AccountRevision
    from app.opening.service import OpeningService

    c = business
    service, asset = asset_case(c)
    generated = service.generate_asset(c.manager, asset["id"])
    with c.db.transaction(actor=c.manager) as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
    OpeningService(c.db).revise_account(
        c.manager,
        asset["expense_account_id"],
        AccountRevision(
            ledger_id=ledger_id,
            code="6602.02",
            category="expense",
            effective_from="2026-01",
            expected_version=1,
            name="变更后的费用科目",
            reason="人工核对科目变更",
        ),
    )
    with pytest.raises(AccountingError) as stale:
        service.approve(
            admin(c),
            generated["draft"]["id"],
            DraftCommand(expected_version=generated["draft"]["version"], reason="拒绝过期科目"),
        )
    assert stale.value.status == 409


def test_native_asset_run_cannot_reference_another_ledger(business):
    from test_finance_agent_reuse import asset_case

    c = business
    _, asset = asset_case(c)
    with pytest.raises(Exception) as denied:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "INSERT INTO periodic_runs(id,book_id,period,kind,run_key,asset_id,input_fingerprint,status,created_by,created_at) VALUES(?,?,?,'fixed',?,?,?,'generated',?,?)",
                (
                    uuid4().hex,
                    c.other_book["id"],
                    "2026-10",
                    uuid4().hex,
                    asset["id"],
                    "0" * 64,
                    c.manager.user_id,
                    "2026-11-08",
                ),
            )
    assert "asset" in str(denied.value).lower()


def test_asset_three_months_cross_year_posts_exact_residual(business):
    from app.opening.models import (
        AccountCreate,
        AccountRevision,
        OpeningPackageCreate,
        OpeningImport,
        OpeningLineInput,
        OpeningCommand,
        PolicyCreate,
    )
    from app.opening.service import OpeningService
    from app.periodic.models import DepreciableAsset, DraftCommand, DraftPost
    from app.periodic.service import PeriodicService
    from app.posting.models import OpeningBind
    from app.posting.service import PostingService
    from app.accounting.invariants import signed_cents_to_money
    from test_posting_service import admin

    c = business
    opening, posting = setup_b03(c)
    service, prep, reviewer = PeriodicService(c.db), OpeningService(c.db), admin(c)
    accounts = []
    for code, name, category, normal in [
        ("6602.03", "跨期折旧费", "expense", "debit"),
        ("1602", "累计折旧", "asset", "credit"),
    ]:
        accounts.append(
            prep.create_account(
                c.manager,
                AccountCreate(
                    ledger_id=opening["ledger_id"],
                    code=code,
                    name=name,
                    category=category,
                    normal_balance=normal,
                    effective_from="2026-01",
                    reason="虚构跨期科目",
                ),
            )
        )
    asset = service.create_asset(
        c.manager,
        DepreciableAsset(
            book_id=c.book["id"],
            name="跨年同一卡片",
            kind="fixed",
            cost="100.00",
            life_months=3,
            start_period="2026-11",
            expense_account_id=accounts[0]["id"],
            expense_account_version=1,
            accum_account_id=accounts[1]["id"],
            accum_account_version=1,
            reason="虚构跨年资产",
        ),
    )
    for key, code, name, category, normal in [
        ("bank", "1002", "银行存款", "asset", "debit"),
        ("payable", "2202", "应付账款", "liability", "credit"),
    ]:
        prep.revise_account(
            c.manager,
            opening[key]["id"],
            AccountRevision(
                ledger_id=opening["ledger_id"],
                expected_version=1,
                code=code,
                name=name,
                category=category,
                normal_balance=normal,
                effective_from="2026-01",
                reason="连续期间有效科目",
            ),
        )
    # New periods receive independently reviewed, balanced opening snapshots.
    # This fixture uses only depreciation balances, never fabricated prior posting.
    prior_total = 0
    for period, amount in [("2026-11", 3333), ("2026-12", 3333), ("2027-01", 3334)]:
        book_id = next_period(c, period)
        # Opening packages reference the policy ID returned by the service.
        policies = prep.list_policies(c.manager, opening["ledger_id"])
        policy_id = policies[-1]["id"]
        if period == "2027-01":
            policy_id = prep.create_policy(
                c.manager,
                PolicyCreate(
                    ledger_id=opening["ledger_id"],
                    standard_kind="small_enterprise",
                    taxpayer_kind="general",
                    effective_from="2027-01",
                    reason="新年政策",
                ),
            )["id"]
        package = prep.create_package(
            c.manager,
            OpeningPackageCreate(
                ledger_id=opening["ledger_id"], target_period=period, policy_id=policy_id
            ),
        )
        lines = []
        for account, balance, account_version in [
            (accounts[0], prior_total, 1),
            (accounts[1], -prior_total, 1),
            (opening["bank"], 1000000, 2),
            (opening["payable"], -1000000, 2),
        ]:
            lines.append(
                OpeningLineInput(
                    account_id=account["id"],
                    account_version=account_version,
                    year_opening=signed_cents_to_money(balance)
                    if period.startswith("2027")
                    else "0.00",
                    ytd_debit=signed_cents_to_money(max(balance, 0))
                    if period.startswith("2026")
                    else "0.00",
                    ytd_credit=signed_cents_to_money(max(-balance, 0))
                    if period.startswith("2026")
                    else "0.00",
                    opening=signed_cents_to_money(balance),
                )
            )
        version = prep.import_version(
            c.manager,
            package["package"]["id"],
            OpeningImport(
                idempotency_key=uuid4().hex, reason="按既有已记账资产余额衔接", lines=lines
            ),
        )["version"]["id"]
        prep.validate_version(c.manager, version)
        command = OpeningCommand(expected_version=1, reason="独立核对跨期原件及期初")
        prep.submit_version(c.manager, version, command)
        prep.approve_version(reviewer, version, command)
        prep.freeze_version(reviewer, version, command)
        posting.bind_opening(
            c.manager,
            OpeningBind(book_id=book_id, package_version_id=version, reason="绑定本期冻结期初"),
        )
        generated = service.generate_asset(c.manager, asset["id"], book_id=book_id)
        assert sum(l["debit_cents"] for l in generated["lines"]) == amount
        draft = service.approve(
            reviewer,
            generated["draft"]["id"],
            DraftCommand(expected_version=generated["draft"]["version"], reason="独立核对本期计提"),
        )
        result = service.post(
            c.manager,
            draft["draft"]["id"],
            DraftPost(
                expected_version=draft["draft"]["version"],
                command_key=uuid4().hex,
                voucher_date=period + "-28",
                reason="复核后记账",
            ),
        )
        assert result["draft"]["status"] == "posted"
        prior_total += amount
        assert service.workspace(c.manager, book_id)["assets"][0]["posted_cents"] == prior_total
        assert (
            PostingService(c.db).account_ledger(c.manager, book_id, accounts[1]["id"])[
                "closing_cents"
            ]
            == -prior_total
        )
    assert prior_total == 10000


def test_reversed_asset_history_requires_manual_reconciliation(business):
    from test_finance_agent_reuse import asset_case
    from test_posting_service import admin
    from app.periodic.models import DraftCommand, DraftPost
    from app.posting.models import ReverseVoucher
    from app.posting.service import PostingService
    from review_policy_fixtures import reverse_reviewed

    c = business
    service, asset = asset_case(c, start="2026-10")
    generated = service.generate_asset(c.manager, asset["id"])
    approved = service.approve(
        admin(c),
        generated["draft"]["id"],
        DraftCommand(expected_version=generated["draft"]["version"], reason="独立复核计提"),
    )
    posted = service.post(
        c.manager,
        generated["draft"]["id"],
        DraftPost(
            expected_version=approved["draft"]["version"],
            command_key=uuid4().hex,
            voucher_date="2026-10-28",
            reason="复核后记账",
        ),
    )
    reverse_reviewed(
        PostingService(c.db),
        c.manager,
        posted["draft"]["posted_voucher_id"],
        ReverseVoucher(
            command_key=uuid4().hex, voucher_date="2026-10-29", reason="核对后冲销历史计提"
        ),
    )
    target = next_period(c, "2026-11")
    with pytest.raises(AccountingError, match="冲销|调整") as blocked:
        service.generate_asset(c.manager, asset["id"], book_id=target)
    assert blocked.value.status == 409


@pytest.mark.parametrize("boundary", ["projection", "api", "native"])
def test_reversed_payment_is_not_available_for_new_allocation(business, boundary):
    from app.accounting.service import now
    from app.posting.models import ReverseVoucher
    from app.posting.service import PostingService
    from review_policy_fixtures import reverse_reviewed

    c = business
    sub, item, voucher = settlement_case(c)
    reverse_reviewed(
        PostingService(c.db),
        c.manager,
        voucher["voucher"]["id"],
        ReverseVoucher(
            command_key=uuid4().hex, voucher_date="2026-10-30", reason="复核后冲销测试付款"
        ),
    )
    if boundary == "projection":
        assert sub.item_workspace(c.manager, c.book["id"], item["id"])["vouchers"] == []
    elif boundary == "api":
        with pytest.raises(AccountingError) as denied:
            allocate(c, sub, item, voucher, None)
        assert denied.value.status == 409
    else:
        with pytest.raises(Exception, match="Corrected payment"):
            with c.db.transaction(write=True, actor=c.manager) as conn:
                conn.execute(
                    "INSERT INTO subledger_allocations(id,firm_id,company_id,book_id,item_id,voucher_id,"
                    "amount_cents,allocated_on,idempotency_key,request_fingerprint,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        c.manager.firm_id,
                        c.company["id"],
                        c.book["id"],
                        item["id"],
                        voucher["voucher"]["id"],
                        300000,
                        "2026-10-20",
                        uuid4().hex,
                        "a" * 64,
                        c.manager.user_id,
                        now(),
                    ),
                )
