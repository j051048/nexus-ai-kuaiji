"""Batch 04: accrual lines are a translation, never a second tax calculation."""

import pytest

from app.accounting.domain import AccountingError
from app.tax import accrual

PACKAGE = {
    "period": "2026-10",
    "source": {
        "kind": "tax_workpaper",
        "id": "wp-1",
        "version": 2,
        "status": "reviewed",
        "digest": "d1",
    },
    "surtax": {"total_cents": 2_100},
    "cit_prepayment": {"due_cents": 25_000},
    "stamp_duty": {"total_cents": 300},
}


def lines(package=None, **kw):
    return accrual.accrual_lines(package or PACKAGE, **kw)


def test_three_accruals_are_balanced_and_use_default_rails():
    result = lines()
    assert result["debit_cents"] == result["credit_cents"] == 2_100 + 25_000 + 300
    accounts = {item["account"] for item in result["entries"]}
    assert {"6403", "2221.03", "6801", "2221.05", "2221.04"} <= accounts
    assert result["source"]["status"] == "reviewed"


def test_zero_amounts_produce_no_lines():
    flat = {
        **PACKAGE,
        "surtax": {"total_cents": 0},
        "cit_prepayment": {"due_cents": 0},
        "stamp_duty": {"total_cents": 0},
    }
    result = lines(flat)
    assert result["entries"] == [] and result["debit_cents"] == 0


def test_unreviewed_source_is_refused():
    draft = {**PACKAGE, "source": {**PACKAGE["source"], "status": "draft"}}
    with pytest.raises(AccountingError) as error:
        lines(draft)
    assert error.value.status == 409
    assert "已复核" in error.value.detail


def test_overpaid_cit_is_not_posted_and_is_explained():
    overpaid = {**PACKAGE, "cit_prepayment": {"due_cents": -5_000}}
    result = lines(overpaid)
    assert all("2221.05" != item["account"] for item in result["entries"])
    assert "多缴" in result["refund_note"]
    assert result["debit_cents"] == result["credit_cents"] == 2_100 + 300


def test_unknown_account_in_chart_is_refused_instead_of_guessed():
    with pytest.raises(AccountingError) as error:
        lines(chart={"6403", "6801", "2221.05", "2221.04"})
    assert error.value.status == 409
    assert "2221.03" in error.value.detail


def test_custom_rails_are_honoured():
    result = lines(accounts={"surtax_payable": "2221.99"})
    assert any(item["account"] == "2221.99" for item in result["entries"])


def test_float_amount_is_refused():
    broken = {**PACKAGE, "stamp_duty": {"total_cents": 3.5}}
    with pytest.raises(AccountingError):
        lines(broken)
