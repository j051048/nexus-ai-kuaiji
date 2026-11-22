"""四税计提 → 现有手工凭证复核流的翻译层测试。

不建新链路：这里只保证翻译出的请求能被 ManualVoucher 接受、键稳定、分录平衡。
"""

import pytest

from app.accounting.domain import AccountingError
from app.posting.models import ManualVoucher
from app.tax.accrual_posting import command_key_for, manual_voucher, normalize_source

ACCOUNTS = {
    "6403": {"id": "acc-6403", "version": 3},
    "2221.03": {"id": "acc-2221-03", "version": 2},
    "6801": {"id": "acc-6801", "version": 1},
    "2221.05": {"id": "acc-2221-05", "version": 1},
}

PACKAGE = {
    "period": "2026-10",
    "source": {
        "workpaper_id": "wp-1",
        "workpaper_version": 2,
        "source_digest": "abc123",
        "status": "reviewed",
    },
}
ENTRIES = [
    {"account": "6403", "side": "debit", "cents": 1250},
    {"account": "2221.03", "side": "credit", "cents": 1250},
    {"account": "6801", "side": "debit", "cents": 300000},
    {"account": "2221.05", "side": "credit", "cents": 300000},
]


def test_voucher_is_balanced_and_carries_account_versions():
    payload = manual_voucher(
        PACKAGE, ENTRIES, ACCOUNTS, book_id="book-1", voucher_date="2026-10-31"
    )
    assert [line["debit"] for line in payload["lines"]] == ["12.50", "0.00", "3000.00", "0.00"]
    assert [line["credit"] for line in payload["lines"]] == ["0.00", "12.50", "0.00", "3000.00"]
    assert payload["lines"][0]["account_version"] == 3
    assert payload["book_id"] == "book-1"
    assert payload["reason"]


def test_command_key_is_stable_for_the_same_workpaper_identity():
    first = command_key_for(PACKAGE["source"])
    second = command_key_for(dict(PACKAGE["source"]))
    assert first == second
    assert first.startswith("tax-accrual:")
    # 换版本就要换键，否则新底稿会被当成旧请求重放。
    assert command_key_for({**PACKAGE["source"], "workpaper_version": 3}) != first


def test_missing_source_identity_refuses_instead_of_guessing():
    with pytest.raises(AccountingError):
        command_key_for({"workpaper_id": "wp-1"})


def test_unknown_account_is_refused():
    with pytest.raises(AccountingError):
        manual_voucher(
            PACKAGE, ENTRIES, {"6403": ACCOUNTS["6403"]}, book_id="b", voucher_date="2026-10-31"
        )


def test_empty_entries_refuse_to_create_an_empty_voucher():
    with pytest.raises(AccountingError):
        manual_voucher(PACKAGE, [], ACCOUNTS, book_id="b", voucher_date="2026-10-31")


def test_unbalanced_entries_are_refused():
    with pytest.raises(AccountingError):
        manual_voucher(
            PACKAGE,
            [{"account": "6403", "side": "debit", "cents": 100}],
            ACCOUNTS,
            book_id="b",
            voucher_date="2026-10-31",
        )


def test_surtax_and_stamp_on_the_same_expense_account_are_merged_for_the_contract():
    # 附加税与印花税默认都记 6403；契约禁止同一 (account_id, aux) 在一个凭证里出现两次。
    entries = [
        {"account": "6403", "side": "debit", "cents": 1250},
        {"account": "2221.03", "side": "credit", "cents": 1250},
        {"account": "6403", "side": "debit", "cents": 300},
        {"account": "2221.04", "side": "credit", "cents": 300},
    ]
    accounts = {**ACCOUNTS, "2221.04": {"id": "acc-2221-04", "version": 1}}
    payload = manual_voucher(PACKAGE, entries, accounts, book_id="b", voucher_date="2026-10-31")
    debits = [line for line in payload["lines"] if line["debit"] != "0.00"]
    credits = [line for line in payload["lines"] if line["credit"] != "0.00"]
    assert len(debits) == 1 and len(credits) == 2  # 6403 合并成一条，两笔应交税费各自保留
    assert debits[0]["account_id"] == "acc-6403"
    assert debits[0]["debit"] == "15.50"
    ManualVoucher(**payload)  # 契约校验通过才是真通过


def test_the_same_account_on_both_sides_is_refused_instead_of_netted():
    entries = [
        {"account": "6403", "side": "debit", "cents": 100},
        {"account": "6403", "side": "credit", "cents": 100},
    ]
    with pytest.raises(AccountingError):
        manual_voucher(PACKAGE, entries, ACCOUNTS, book_id="b", voucher_date="2026-10-31")


def test_both_source_spellings_yield_the_same_command_key():
    # four_tax.package 用 id/version/digest，本模块用 workpaper_*；两种写法必须同键，
    # 否则同一张底稿会生成两张计提凭证。
    package_spelling = {"id": "wp-1", "version": 2, "digest": "abc123", "status": "reviewed"}
    assert command_key_for(package_spelling) == command_key_for(PACKAGE["source"])
    assert normalize_source(package_spelling)["workpaper_id"] == "wp-1"


def test_voucher_date_outside_the_period_is_refused():
    with pytest.raises(AccountingError):
        manual_voucher(PACKAGE, ENTRIES, ACCOUNTS, book_id="b", voucher_date="2026-11-01")
