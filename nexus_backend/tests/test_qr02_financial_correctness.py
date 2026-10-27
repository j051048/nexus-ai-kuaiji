"""Q-R02: 历史金额稳定、缺期初受控、辅助维度连续、导出来源绑定。

这些用例在修复前应当是红的：
- `_accounts` 取 `current_version`，新增子科目或改名会改写已过账月份的名称、层级与合计；
- `_balance_rows` 只对叶子行求合计，父科目变成汇总后其自身发生额仍然显示却从合计消失；
- 范围内没有任何已绑定期初时 `next(status=="ok")` 抛 `StopIteration`（500 而不是受控结果）；
- 连续性只按 `account_id` 汇总，辅助对象换了身份但合计不变时无法发现；
- 导出来源摘要不含科目身份，改名后旧 CSV 仍然返回同样的来源指纹。
"""

from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.ledgers.models import PeriodLink
from app.ledgers.service import LedgersService
from app.opening.models import (
    AccountCreate,
    AccountRevision,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
)
from app.opening.service import OpeningService
from app.posting import presentation
from app.posting.presentation import export_formal_book
from test_business import business
from test_posting_service import admin, ledger_of
from test_q03_continuous_books import bound, post

__all__ = ["business"]


def _account(case, code):
    with case.db.transaction() as conn:
        return dict(
            conn.execute(
                "SELECT a.id,v.version,v.name,v.is_leaf FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
                "WHERE a.ledger_id=? AND v.code=?",
                (ledger_of(case), code),
            ).fetchone()
        )


def _rows(result):
    return {
        (row["code"], row["name"], row["is_leaf"]): (
            row["opening_cents"],
            row["debit_cents"],
            row["credit_cents"],
            row["closing_cents"],
        )
        for row in result["rows"]
    }


def _snapshot(case, posting):
    result = posting.trial_balance(case.manager, case.book["id"])
    return _rows(result), dict(result["totals"])


def _add_child_account(case, parent_code, code, name, effective_from):
    parent = _account(case, parent_code)
    return OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=ledger_of(case),
            code=code,
            name=name,
            parent_account_id=parent["id"],
            effective_from=effective_from,
            reason="Q-R02 未来月份新增子科目",
        ),
    )


def _rename(case, code, name, effective_from):
    account = _account(case, code)
    return OpeningService(case.db).revise_account(
        case.manager,
        account["id"],
        AccountRevision(
            ledger_id=ledger_of(case),
            code=code,
            name=name,
            effective_from=effective_from,
            reason="Q-R02 未来月份科目更名",
            expected_version=account["version"],
        ),
    )


def test_history_keeps_the_parent_own_amount_after_a_child_is_added(business):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "qr02-parent-001", "2026-10-09", "1200.00", "1200.00")
    before, totals_before = _snapshot(case, posting)

    _add_child_account(case, "1002", "1002.01", "工商银行基本户", "2026-11")

    after, totals_after = _snapshot(case, posting)
    assert after == before, "下月新增子科目不得改写已过账月份的科目、金额或层级"
    assert totals_after == totals_before, "合计必须按账簿自身发生额计算，不能因父子层级变化而消失"


def test_history_keeps_the_posted_name_after_a_rename(business):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "qr02-name-001", "2026-10-10", "30.00", "30.00")
    before, _totals = _snapshot(case, posting)
    original = next(name for code, name, _leaf in before if code == "1002")

    _rename(case, "1002", "银行存款-基本户", "2026-11")

    after, _totals = _snapshot(case, posting)
    assert after == before, f"历史月份仍应显示 {original}，不能被下月更名改写"


def test_range_without_any_bound_opening_returns_incomplete_not_an_error(business):
    case = business
    posting, _opening = bound(case)
    november = case.ledger.create_book(
        case.manager,
        BookCreate(name="2026-11 账套", period="2026-11", company_id=case.company["id"]),
    )
    with case.db.transaction() as conn:
        own_ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (november["id"],)
        ).fetchone()["ledger_id"]
    LedgersService(case.db).link_period(
        case.manager,
        case.book["id"],
        PeriodLink(
            source_book_id=november["id"],
            reason="Q-R02 未绑定期初的次月账套",
            expected_ledger_id=own_ledger,
        ),
    )

    result = posting.range_trial_balance(case.manager, ledger_of(case), "2026-10", "2026-11")
    assert result["complete"] is False
    assert {blocker["code"] for blocker in result["blockers"]} == {
        "opening_missing",
        "period_open",
    }
    assert result["coverage"][1]["status"] == "unbound"

    only = posting.range_trial_balance(case.manager, ledger_of(case), "2026-11", "2026-11")
    assert only["complete"] is False
    assert only["scope"]["book_id"] == november["id"]
    assert only["rows"] == []


def test_auxiliary_identity_change_is_an_opening_mismatch(business):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "qr02-aux-001", "2026-10-11", "15.00", "15.00")
    with case.db.transaction() as conn:
        accounts = {
            row["code"]: (row["id"], row["version"])
            for row in conn.execute(
                "SELECT a.id,v.version,v.code FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
                "WHERE a.ledger_id=?",
                (ledger_of(case),),
            )
        }
    book = case.ledger.create_book(
        case.manager,
        BookCreate(name="2026-11 账套", period="2026-11", company_id=case.company["id"]),
    )
    with case.db.transaction() as conn:
        own_ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()["ledger_id"]
    LedgersService(case.db).link_period(
        case.manager,
        case.book["id"],
        PeriodLink(
            source_book_id=book["id"],
            reason="Q-R02 辅助维度连续性账套",
            expected_ledger_id=own_ledger,
        ),
    )
    opening = OpeningService(case.db)
    with case.db.transaction() as conn:
        policy_id = conn.execute(
            "SELECT policy_id FROM opening_packages WHERE ledger_id=? AND target_period=?",
            (ledger_of(case), "2026-10"),
        ).fetchone()["policy_id"]
    package = opening.create_package(
        case.manager,
        OpeningPackageCreate(
            ledger_id=ledger_of(case), target_period="2026-11", policy_id=policy_id
        ),
    )
    version = opening.import_version(
        case.manager,
        package["package"]["id"],
        OpeningImport(
            idempotency_key="qr02-aux-opening",
            reason="Q-R02 期初换辅助对象但合计不变",
            lines=[
                OpeningLineInput(
                    account_id=accounts["1002"][0],
                    account_version=accounts["1002"][1],
                    auxiliary_values={"bank": "B"},
                    year_opening="10000.30",
                    ytd_debit="0.00",
                    ytd_credit="0.00",
                    opening="10000.30",
                ),
                OpeningLineInput(
                    account_id=accounts["2202"][0],
                    account_version=accounts["2202"][1],
                    year_opening="-10000.30",
                    ytd_debit="0.00",
                    ytd_credit="0.00",
                    opening="-10000.30",
                ),
            ],
        ),
    )
    opening.validate_version(case.manager, version["version"]["id"])
    opening.submit_version(
        case.manager,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="提交辅助维度期初"),
    )
    reviewer = admin(case)
    opening.approve_version(
        reviewer,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="独立核准辅助维度期初"),
    )
    opening.freeze_version(
        reviewer,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="冻结辅助维度期初"),
    )

    result = posting.range_trial_balance(case.manager, ledger_of(case), "2026-10", "2026-11")
    codes = [blocker["code"] for blocker in result["blockers"]]
    assert "opening_missing" in codes, "未绑定应如实报告，而不是当作已衔接"
    assert result["complete"] is False


def test_export_refuses_a_file_rendered_before_a_rename(business, monkeypatch):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "qr02-export-001", "2026-10-12", "40.00", "40.00")
    original = presentation._render_csv

    def render_then_rename(*args, **kwargs):
        content = original(*args, **kwargs)
        _rename(case, "1002", "银行存款-改名后", "2026-10")
        return content

    monkeypatch.setattr(presentation, "_render_csv", render_then_rename)
    with pytest.raises(AccountingError) as error:
        export_formal_book(posting, case.manager, case.book["id"], "trial_balance")
    assert error.value.status == 409
    assert "变化" in error.value.detail
