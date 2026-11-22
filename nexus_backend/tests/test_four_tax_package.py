"""Batch 04: four-tax package composes from the reviewed VAT workpaper only."""

from contextlib import contextmanager

import pytest

from app.accounting.domain import AccountingError
from app.tax import four_tax

REVIEWED = {
    "id": "wp-1",
    "version": 3,
    "tax_cents": 30_000,
    "surtax_cents": 3_600,
    "credit_carry_cents": 0,
    "ledger_cents": 30_000,
    "source_digest": "abc123",
    "status": "reviewed",
}


class Conn:
    def __init__(self, row):
        self.row = row
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        return self

    def fetchone(self):
        return self.row


class Service:
    def __init__(self, row):
        self.conn = Conn(row)
        self.access = self
        self.db = self

    @contextmanager
    def transaction(self, **_):
        yield self.conn

    def book(self, conn, actor, book_id, capability):
        return {"id": book_id, "period": "2026-10"}


def run(row=REVIEWED, **kwargs):
    defaults = dict(
        surtax_items=[{"code": "city", "rate_bp": 700}],
        cumulative_profit_cents=100_000,
        cit_rate_bp=2_500,
    )
    return four_tax.package(Service(row), object(), "book-1", "2026-10", **{**defaults, **kwargs})


def test_surtax_is_computed_on_the_payable_vat():
    result = run()
    assert result["vat"]["tax_cents"] == 30_000
    assert result["surtax"]["total_cents"] == 2_100  # 30_000 * 7%
    assert result["source"]["id"] == "wp-1" and result["source"]["status"] == "reviewed"


def test_cit_uses_the_supplied_profit_not_the_ledger():
    result = run(cumulative_profit_cents=100_000)
    assert result["cit_prepayment"]["due_cents"] == 25_000
    other = run(cumulative_profit_cents=0)
    assert other["cit_prepayment"]["due_cents"] == 0


def test_cit_without_a_stated_profit_is_refused_instead_of_using_the_ledger_tax():
    # ledger_cents 是账载税额，不是利润；拿它当所得税计税基础会记出一笔假税。
    with pytest.raises(AccountingError) as error:
        run(cumulative_profit_cents=None)
    assert error.value.status == 409
    assert "累计利润" in error.value.detail


def test_unreviewed_or_missing_workpaper_is_refused():
    with pytest.raises(AccountingError) as error:
        run(row=None)
    assert error.value.status == 409
    assert "已复核的增值税底稿" in error.value.detail


def test_stamp_duty_refuses_duplicate_contract():
    contract = {"key": "c-1", "kind": "purchase", "cents": 1_000_000}
    with pytest.raises(AccountingError):
        run(
            contracts=[contract, contract],
            stamp_rates={"purchase": 30},
        )


def test_package_lists_the_four_kinds_and_money_is_integer():
    result = run(
        contracts=[{"key": "c-1", "kind": "purchase", "cents": 1_000_000}], stamp_rates={"purchase": 30}
    )
    assert result["kinds"] == ["vat", "surtax", "cit_prepayment", "stamp_duty"]
    assert isinstance(result["surtax"]["total_cents"], int)
    assert isinstance(result["stamp_duty"]["total_cents"], int)
